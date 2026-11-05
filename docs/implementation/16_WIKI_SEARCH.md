# Stage 16 — Wiki search (US-10)

> **Story ([US-10](../USER_STORIES_LOG.md#us-10)):** as a member I want to search the wiki in my own
> words, including Polish word forms, so that I can find articles without paging through the list
> and without exposing article text in search results.

## Refined requirement

Refinement decisions (accepted 2026-11-05):

| Topic | Decision |
|---|---|
| What is searched | Titles, article text of visible blocks, and author names. |
| What results show | **Title, authors, verification date and freshness only.** No snippets, no highlighted text, no match counts. |
| Which articles | CANONICAL, visible articles only, matching `GET /wiki/articles`. |
| Overlapping articles | **No deduplication or penalty.** Several articles on one topic are welcome ([owner decision](../USER_STORIES_LOG.md#us-10)); results are ranked only by relevance to the query. |
| Polish | Word forms match („pszczół” finds „pszczoły”) and accents are optional. |
| Out of scope | Semantic search, typo tolerance, search across private notes, search in the atlas, saved searches. |

## Acceptance criteria

```gherkin
Background: member1 is logged in; the article "Leczenie warrozy" is canonical

Scenario: word forms and missing accents
  When member1 searches for "warroza", "warrozy" or "leczenie warroze"
  Then "Leczenie warrozy" is found
  When member1 searches for "pszczol"
  Then articles containing "pszczoły" are found

Scenario: results reveal no article text
  When member1 searches for a phrase that appears only in an article's body
  Then the article is found
  And the response holds its title, authors, verification date and freshness
  But no text from any block
  And member1's coverage budget does not change until they open the article

Scenario: withdrawn and hidden text cannot be found
  Given a block contains the word "sekret123"
  When its author suspends or revokes it
  Then the next search for "sekret123" does not find the article
  When the author resumes it
  Then the search finds it again
  And text in honeypots, drafts, superseded and rejected articles is never found

Scenario: limits
  When member1 exceeds the search limit
  Then the API answers 429 rate_limited and the page says so
  When Redis is unavailable
  Then the API answers 503 search_unavailable and the article list still works
```

## Design

### Index (migration `0030_wiki_search`)

- Text search configuration `spoldzielnia_search`: a copy of `simple` with `unaccent`. PostgreSQL has
  no built-in Polish stemmer, and the configuration ICS uses would not match word forms.
- Table `article_search (article_id PK/FK ON DELETE CASCADE, document tsvector, updated_at)` with a
  GIN index. It holds lemmas only, with no way to read them through the API.
- Weights: title A, text B, authors C.

### Rules

- **R1. Lemmas come from a dictionary, not from the spaCy model.** spaCy is only in the `worker-core`
  image, not in `api`, and its lemmas depend on the sentence, so a two-word query could produce
  different lemmas from the same words in an article. `simplemma` 2.0.0 (MIT, released 2026-08-12,
  no required dependencies, 19 MB wheel) gives the same result in both places, with languages
  `("pl", "en")`. Chunk 16.1 checks its quality before anything else depends on it (Risks),
  including inflected words typed without accents, which a dictionary may not recognise.
- **R2. The index changes in the transaction that changes visibility.** `wiki/search_index.py`
  exposes `refresh(session, article_ids)`, called from `change_status` and from the consent service.
  It indexes ACTIVE, non-honeypot blocks of CANONICAL, visible articles, and otherwise deletes the
  row. A suspended block therefore cannot be found after the suspend commits.
- **R3. Double check on read.** The query joins `wiki_article` and repeats the status and `is_hidden`
  filters, so an out-of-date row cannot expose an article.
- **R4. The response cannot carry text.** `SearchHit` has no text field, and `ts_headline` is
  forbidden. An import-linter contract stops `wiki.search` importing `rag`, `vector` or `shield`.
  An integration test asserts that no block text appears in the response bytes.
- **R5. Search is limited, and fails closed.** `GET /wiki/search?q=&limit=&offset=`: `q` 2-200
  characters, at most 20 hits a page and 5 pages, 60 queries an hour per user (configurable in a
  new `SearchSettings`). Redis down means 503, because this limit protects knowledge and is
  not a mere preference. There is no total count. Prefix matching applies only to the last word, and
  only from 3 characters.
- **R6. Queries stay private.** Query text is not audited or logged, following the rule against
  storing question text. Refused queries count as a risk signal in the shield (the module and weight
  are settled in 16.2, against `shield/` as it stands).
- **R7. Rebuild is a command.** The index is derived data. `spoldzielnia-ai search-rebuild` is
  idempotent; `bootstrap` runs it when the table is empty, and the DR rehearsal verifies it. The
  nightly `vector-consistency-audit` also reports articles whose index row is missing or out of date.

### Ranking

`ts_rank` with the weights above, an exact title match first, then the article id for a stable
order. Freshness does not change the rank: overdue articles stay findable.

## Chunks

- **16.1 Lemmatizer check.** Add `simplemma` (locked). Evaluate it on a fixture of about 200
  inflected Polish domain words (beekeeping, law, farming), with a minimum agreed recall recorded in
  the test. Measure import time and memory in the `api` container. *Tests:* unit fixture; a query and
  a document lemmatize alike. **If it fails, stop and return with the fallback (Risks).**
- **16.2 Index.** Migration, `search_index.refresh`, hooks in governance and consent, rebuild
  command, bootstrap step, consistency report. *Tests:* integration for every status change and
  consent action, honeypots, RLS unaffected, migration, table registration.
- **16.3 API.** Endpoint, limits, ranking, response model, import contract, risk signal, `gen:api`.
  *Tests:* integration (no text in the response, coverage untouched, paging cap, 429, 503, accents).
- **16.4 Frontend.** Search box on `/wiki`: 300 ms debounce, `?q=` in the address, aborted stale
  requests, and separate loading, empty, error and limit states. Works at 360 px, targets of at least
  44 px. *Tests:* Vitest in both languages, with no stale result shown.
- **16.5 E2E and closure.** E2E scenario 18, `SECURITY_REVIEW.md` section, plan sections.

## Risks and notes

- **Accepted: search is an oracle.** Anyone can ask whether a word occurs in an article and see the
  answer, so a very patient member could learn vocabulary and which articles use it. They cannot
  retrieve text, and the limits slow this down. Compared with reading an article, which is budgeted,
  this is the cost of full-text search; the owner chose it over titles-only on 2026-11-05.
- **`simplemma` may not be good enough for domain terms.** The fallback is a Polish Hunspell
  dictionary loaded into PostgreSQL. It needs a custom Postgres image (and handling of its effect on
  restore), so it is not the first choice.
- **Size of the `api` image and memory.** Measured in 16.1; lazy loading keeps the cost out of
  requests that do not search.
- **Renames.** A changed `display_name` updates the index on the next rebuild, not at once. This does
  not expose any text, so it is acceptable.
