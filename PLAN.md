## Instrukcje Systemowe i Wymogi Jakościowe dla Agenta

* **Język i Toolchain:** Python 3.12+, zarządzanie pakietami przez `uv`, formatowanie i linting przez `ruff`, ścisłe sprawdzanie typów przez `pyright --strict`.
* **Stos technologiczny:** FastAPI, SQLAlchemy 2.0 (async) + PostgreSQL 16 (z aktywnym Row-Level Security), Qdrant, Redis + Celery, LiteLLM, Authlib / FastAPI-Security (OAuth2/OIDC), Microsoft Presidio + `spaCy` (`pl_core_news_lg`), LLM-Guard.
* **Standardy Bezpieczeństwa (Zero-Trust):**
* **OWASP API Security Top 10:** Ochrona przed BOLA/IDOR na poziomie silnika bazy danych (Postgres RLS), ochrona przed SSRF przy pobieraniu linków, ścisła walidacja uploadowanych plików.
* **OWASP Top 10 for LLM (2025):** Ochrona przed Indirect Prompt Injection (LLM01), wyciekiem PII (LLM02), nadmierną autonomią (LLM06) oraz kradzieżą/destylacją modelu i bazy RAG (LLM10).


* **Zasada pracy Agenta:** Realizuj plan etapami. Każdy etap musi kończyć się uruchomieniem `pytest`, `ruff check` oraz `pyright`. Zakaz przechodzenia dalej przy błędach typowania lub testów.

---

## Etap 1: Fundament, OAuth2/OIDC i Izolacja Bazy Danych (Postgres RLS)

* **Zadania:**
* Inicjalizacja monorepo z użyciem `uv` oraz konfiguracja `docker-compose.yml` (PostgreSQL 16, Qdrant z kluczem API i TLS, Redis, Keycloak jako lokalny dostawca OIDC do testów, ClamAV).
* Implementacja uwierzytelniania **OAuth2 / OpenID Connect (Authorization Code Flow z PKCE)**:
* Brak przechowywania haseł w bazie aplikacji – pełna delegacja do dostawcy tożsamości (Keycloak / Google Workspace / Microsoft Entra ID).
* Sesje oparte o ciasteczka `HttpOnly`, `Secure`, `SameSite=Strict`, krótko żyjące tokeny dostępu (15 min) i rotację Refresh Tokenów z detekcją ponownego użycia.
* Wymuszenie skonfigurowanego MFA w tokenie OIDC (claim `amr` / `acr`) dla ról `EXPERT` oraz `ADMIN`.


* Modele bazodanowe z ochroną **OWASP BOLA/IDOR (Row-Level Security)**:
* Tabele: `User`, `PrivateNote`, `WikiArticle`, `ArticleBlock` (z polami `author_id`, `consent_status`), `ExpertReview`, `SecurityAuditLog`.
* Wdrożenie polityk `CREATE POLICY` w PostgreSQL dla tabeli `PrivateNote`: baza danych na poziomie silnika odrzuca zapytania bez ustawionej zmiennej sesyjnej `app.current_user_id` równej `owner_id`.




* **Weryfikacja (Testy):**
* Testy logowania OIDC (mock tokenów JWT z różnymi rolami i statusem MFA).
* Test próby odczytu `PrivateNote` innego użytkownika bezpośrednio przez sesję SQLAlchemy (weryfikacja blokady przez Postgres RLS).



---

## Etap 2: Bezpieczny Upload Plików i Potok Multimediów (OWASP File Security & SSRF)

* **Zadania:**
* Stworzenie bezpiecznego potoku wgrywania plików do `PrivateNote`:
* Weryfikacja typu pliku po nagłówkach binarnych (*magic bytes* przez `python-magic`), odrzucanie plików wykonywalnych i archiwów wielokrotnie zagnieżdżonych (ochrona przed *Zip Bomb*).
* Skanowanie antywirusowe każdego pliku przez demona ClamAV przed zapisem w storage (MinIO/S3).
* Automatyczne czyszczenie metadanych EXIF ze zdjęć (ochrona prywatności przed przypadkowym wyciekiem lokalizacji GPS pasieki) chyba że użytkownik jawnie zaznaczy opcję zachowania lokalizacji.


* Ochrona przed **OWASP SSRF (Server-Side Request Forgery)**:
* Moduł pobierający zewnętrzne linki do notatnika musi rozwiązywać DNS i blokować ruch do adresów prywatnych (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.169.254`).


* Zadania Celery: transkrypcja (`faster-whisper`), opis obrazów (VLM), parsowanie PDF/DOCX (`Docling`) uruchamiane w odizolowanych kontenerach bez dostępu do sieci zewnętrznej.


* **Weryfikacja (Testy):**
* Próba wgrania pliku `.exe` ze zmienionym rozszerzeniem na `.pdf` (musi zwrócić HTTP 415).
* Próba dodania linku wskazującego na `[http://127.0.0.1:8000](http://127.0.0.1:8000)` lub `[http://169.254.169.254](http://169.254.169.254)` (musi zostać zablokowana przez filtr SSRF).



---

## Etap 3: Ochrona OWASP LLM01/LLM02, Przepływ „Push-to-Wiki” i PII Scrubbing

* **Zadania:**
* Integracja **Microsoft Presidio** z polskim modelem językowym `spaCy` (`pl_core_news_lg`) oraz własnymi rozpoznawaczami (regex + suma kontrolna) dla polskich identyfikatorów: **NIP, PESEL, REGON, KRS, IBAN, numery działek ewidencyjnych i numery weterynaryjne pasiek**.
* Ochrona przed **Indirect Prompt Injection (OWASP LLM01)** na etapie Ingestion i promocji notatki:
* Skanowanie tekstu z dokumentów i transkrypcji przez `LLM-Guard` (detekcja ukrytych instrukcji sterujących typu *„Ignore previous instructions”*).
* Enkapsulacja danych wejściowych w separatorach XML z jawną instrukcją systemową traktującą zawartość jako czyste dane, nigdy jako polecenia.


* Endpoint `POST /notes/promote-to-wiki`: lokalny model LLM przekształca oczyszczone z PII notatki w bloki `ArticleBlock` przypisane do `author_id`.


* **Weryfikacja (Testy):**
* Test z dokumentem zawierającym prawdziwy numer NIP, PESEL oraz złośliwy prompt wstrzyknięty białą czcionką w PDF – weryfikacja usunięcia PII i zflagowania próby Prompt Injection.



---

## Etap 4: Silnik Governance, Zgody Twórców (Exit Strategy) i Indeks Qdrant

* **Zadania:**
* Konfiguracja kolekcji `private_notes_index` oraz `canonical_wiki_index` w Qdrant.
* Implementacja procesu recenzji eksperckiej (`DRAFT` ➔ `IN_REVIEW` ➔ `CANONICAL`) oraz zastępowania wiedzy (`SUPERSEDED`).
* Implementacja endpointów **Exit Strategy** (`/me/contributions/suspend` oraz `/me/contributions/revoke`):
* Natychmiastowe usuwanie lub dezaktywacja wektorów w Qdrant po kluczu `author_id`.
* Degradacja artykułów wieloautorskich do statusu `REVISION_REQUIRED` i wycofanie ich z aktywnego indeksu RAG po wycofaniu zgody przez jednego ze współautorów.
* Zapis każdej zmiany uprawnień i zgód w niemodyfikowalnym dzienniku `SecurityAuditLog`.




* **Weryfikacja (Testy):**
* Test kaskadowego wycofania zgody współautora i sprawdzenie czasu propagacji zmian w Qdrant (brak możliwości pobrania wycofanych bloków natychmiast po zakończeniu transakcji HTTP).



---

## Etap 5: Tarcza Anty-Destylacyjna (Anti-Distillation Shield) i Bezpieczny RAG

* **Zadania:**
* Implementacja middleware'u `AntiDistillationGuard` wpiętego przed i po wywołaniu `RAGService`:
* **Moduł `CoverageTracker` (Redis HyperLogLog / Set):** śledzenie unikalnych `block_id` oraz `article_id` pobranych przez `user_id` w oknach 24h i 7d. Blokada zapytania (HTTP 429 / 403 Security Lock), gdy użytkownik przekroczy konfigurowalny próg pokrycia bazy (domyślnie 15% całej bazy w 7 dni).
* **Moduł `SemanticAnomalyDetector`:** obliczanie średniej odległości cosinusowej między kolejnymi zapytaniami użytkownika; wykrywanie zautomatyzowanego skanowania słownikowego i enumeracji tematów.
* **Moduł `HoneypotInjector`:** narzędzie CLI/API dla administratora do wstrzykiwania ukrytych bloków-kanarków (`is_honeypot = True`) do `canonical_wiki_index`. Pobranie bloku-kanarka w wyniku zapytania ekstrakcyjnego natychmiast podnosi `user_risk_score` i wysyła alert bezpieczeństwa.
* **Moduł `OutputVerifier` & `Watermarker`:** blokada odpowiedzi mających powyżej 70% pokrycia dosłownego (n-gram overlap) z surowym artykułem źródłowym; wstrzykiwanie deterministycznego znaku wodnego w generowaną syntezę.


* Implementacja `RAGService` ze ścisłym uziemieniem (Strict Grounding) i walidacją wyjścia przez `Pydantic` (wymuszenie poprawnych cytowań bez ujawniania surowych wektorów).


* **Weryfikacja (Testy):**
* **Test symulujący atak destylacyjny:** skrypt wykonujący serię 50 zapytań próbujących pobrać kolejne rozdziały bazy wiedzy – weryfikacja, czy `CoverageTracker` i `SemanticAnomalyDetector` odcinają dostęp przed pobraniem 15% bazy.
* **Test ataku na ekstrakcję dosłowną:** zapytanie *„Zacytuj w całości artykuł nr 12”* – weryfikacja zablokowania odpowiedzi przez `OutputVerifier`.
* **Test kanarka:** wywołanie zapytania trafiającego w rekord `is_honeypot = True` – weryfikacja utworzenia wpisu o incydencie w `SecurityAuditLog`.



---

## Etap 6: Frontend, Nagłówki Bezpieczeństwa (OWASP Web) i Audyt Końcowy

* **Zadania:**
* Budowa interfejsu w Next.js z logowaniem OIDC, edytorem blokowym (BlockNote/TipTap), panelem recenzji eksperckiej i panelem zarządzania własnością intelektualną twórcy.
* Zabezpieczenie warstwy prezentacji przed kopiowaniem masowym i atakami przeglądarkowymi:
* Ścisłe nagłówki **CSP (Content Security Policy)**, `X-Frame-Options: DENY`, `Strict-Transport-Security`.
* Zabezpieczenie przed *DOM-based XSS* przy renderowaniu odpowiedzi Markdown z LLM oraz artykułów Wiki (sanityzacja HTML przez `DOMPurify`).


* Widok Bezpieczeństwa dla Administratora Spółdzielni: podgląd `SecurityAuditLog`, statystyk pokrycia bazy przez użytkowników (`CoverageTracker`) oraz alertów z kanarków.


* **Weryfikacja (Testy E2E + Bezpieczeństwo):**
* Automatyczny skan zależności (`uv pip audit` / `bandit` dla kodu Pythona).
* Testy E2E (Playwright) weryfikujące pełny przepływ uprawnień, izolację notatników i blokadę konta przy próbie masowego wyciągania wiedzy z czatu.

---

## Szczegółowa Lista Zadań Realizacyjnych (Chunked Implementation Checklist)

Pełna specyfikacja techniczna i architektura wdrożenia w kontenerach Docker znajduje się w:
* [docs/implementation/00_OVERVIEW.md](docs/implementation/00_OVERVIEW.md)
* [docs/implementation/DEPLOYMENT_DOCKER.md](docs/implementation/DEPLOYMENT_DOCKER.md)

### Etap 1: Fundament, OAuth2/OIDC i Izolacja Bazy Danych (Postgres RLS)
*Dokumentacja: [docs/implementation/01_FOUNDATION_AUTH_RLS.md](docs/implementation/01_FOUNDATION_AUTH_RLS.md)*
- [x] 1.1 Inicjalizacja monorepo i toolchainu (`uv`, `ruff`, `pyright`, `pytest`, `Settings`, `cli.py`)
- [x] 1.2a Konteneryzacja usług pomocniczych w Docker Compose (PostgreSQL, Qdrant TLS, Redis, Keycloak, ClamAV, MinIO, Docker secrets)
- [x] 1.2b Obraz API backendu, reverse proxy (Caddy HTTPS) i test dymny (smoke test)
- [x] 1.3 Warstwa bazy danych, role uprawnień (owner vs app) i modele domenowe SQLAlchemy 2.0
- [x] 1.4 Izolacja Row-Level Security (Postgres RLS) i niemodyfikowalny audyt (SecurityAuditLog hash chain)
- [x] 1.5 Uwierzytelnianie OIDC Authorization Code Flow z PKCE (wzorzec BFF)
- [x] 1.6 Sesje ciasteczkowe `__Host-`, rotacja tokenów odświeżających, ochrona CSRF, RBAC i wymuszenie MFA

### Etap 2: Bezpieczny Upload Plików i Potok Multimediów (OWASP File Security & SSRF)
*Dokumentacja: [docs/implementation/02_UPLOADS_SSRF_MEDIA.md](docs/implementation/02_UPLOADS_SSRF_MEDIA.md)*
- [x] 2.1 API notatnika prywatnego z szyfrowaniem kopertowym (AES-256-GCM) i storage MinIO
- [x] 2.2 Weryfikacja typu plików (magic bytes), ochrona przed archiwami zagnieżdżonymi (Zip Bomb)
- [x] 2.3 Skanowanie antywirusowe ClamAV, czyszczenie metadanych EXIF i endpoint uploadu
- [x] 2.4 Moduł pobierania linków odporny na SSRF (walidacja DNS, blokada adresów prywatnych)
- [x] 2.5 Zadania Celery przetwarzania multimediów w odizolowanych sieciowo kontenerach workerów

### Etap 3: Ochrona OWASP LLM01/LLM02, Przepływ „Push-to-Wiki” i PII Scrubbing
*Dokumentacja: [docs/implementation/03_PII_INJECTION_PROMOTION.md](docs/implementation/03_PII_INJECTION_PROMOTION.md)*
- [x] 3.1 Polskie rozpoznawacze PII (PESEL, NIP, REGON, KRS, IBAN, działki, WNI) z walidacją sum kontrolnych
- [x] 3.2 Usługa anonimizacji Microsoft Presidio ze spaCy (`pl_core_news_lg`) i stałymi pseudonimami
- [x] 3.3 Wielowarstwowa detekcja Prompt Injection (LLM-Guard, polskie reguły, ukryty tekst w PDF)
- [x] 3.4 Bramka modeli LiteLLM (wymuszenie lokalnego LLM) i bezpieczny generator promptów XML
- [x] 3.5 Endpoint i potok `POST /notes/promote-to-wiki` (tworzenie bloków z provenance)

### Etap 4: Silnik Governance, Zgody Twórców (Exit Strategy) i Indeks Qdrant
*Dokumentacja: [docs/implementation/04_GOVERNANCE_CONSENT_QDRANT.md](docs/implementation/04_GOVERNANCE_CONSENT_QDRANT.md)*
- [x] 4.1 Kolekcje i repozytoria wektorowe Qdrant z twardymi filtrami uprawnień (tenant i active canonical)
- [x] 4.2 Maszyna stanów recenzji eksperckiej (`DRAFT` -> `IN_REVIEW` -> `CANONICAL` / `SUPERSEDED` / `DISPUTED`)
- [x] 4.3 Endpointy wycofania zgód Exit Strategy (`suspend`, `resume`, `revoke`) z kaskadą na artykuły
- [x] 4.4 Tabela Outbox, zadania uzgadniania Qdrant w Celery Beat i audyt dryfu wektorów

### Etap 5: Tarcza Anty-Destylacyjna (Anti-Distillation Shield) i Bezpieczny RAG
*Dokumentacja: [docs/implementation/05_ANTI_DISTILLATION_RAG.md](docs/implementation/05_ANTI_DISTILLATION_RAG.md)*
- [x] 5.1 Usługa `RAGService` ze ścisłym uziemieniem (strict grounding) i schematami bez surowych chunków
- [x] 5.2 Moduł `CoverageTracker` (budżet pokrycia bazy w oknach 24h, 7d, 30d w Redis HLL)
- [x] 5.3 Moduł `SemanticAnomalyDetector` (analiza anomalii odległości w przestrzeni embeddingów)
- [x] 5.4 Moduł `HoneypotInjector` (bloki-kanarki, usuwanie przed syntezą, natychmiastowe alerty)
- [x] 5.5 Moduł `OutputVerifier` (blokada dosłownej ekstrakcji) i deterministyczny `Watermarker`
- [x] 5.6 Middleware `AntiDistillationGuard`, silnik ryzyka i progresywna degradacja (step-up auth / PoW)

### Etap 6: Frontend, Nagłówki Bezpieczeństwa (OWASP Web) i Audyt Końcowy
*Dokumentacja: [docs/implementation/06_FRONTEND_AUDIT.md](docs/implementation/06_FRONTEND_AUDIT.md)*
- [x] 6.1 Rusztowanie Next.js (App Router, Tailwind v4), kontener frontendowy i restrykcyjne nagłówki CSP
- [x] 6.2 Interfejs prywatnego notatnika (BlockNote, obsługa uploadu i kwarantanny)
- [x] 6.3 Interfejs bazy wiedzy Wiki, widok autorstwa bloków i panel recenzji eksperckiej
- [x] 6.4 Panel praw autorskich twórcy (zarządzanie kontrybucjami, suspend / revoke)
- [x] 6.5 Interfejs czatu spółdzielczego RAG ze sprawdzonym renderowaniem Markdown
- [x] 6.6 Panel bezpieczeństwa i monitoringu dla Administratora Spółdzielni
- [x] 6.7 Automatyczne skanowanie podatności (`pip-audit`, `bandit`, `trivy`) i testy E2E Playwright
- [x] 6.8 Wdrożenie produkcyjne na serwerze Linux (`docker-bake`, rejestr, `deploy.sh`, `backup.sh`, disaster recovery)


---

## Rozszerzenia poza pierwotny plan (User Stories)

Rejestr historyjek dodanych poza Etapami 1–6: [docs/USER_STORIES_LOG.md](docs/USER_STORIES_LOG.md)

### Etap 7: Konfiguracja modeli LLM w panelu administratora (US-01)
*Dokumentacja: [docs/implementation/07_LLM_MODEL_CONFIG.md](docs/implementation/07_LLM_MODEL_CONFIG.md)*
- [x] 7.1 Model danych konfiguracji LLM (dostawcy, modele, rewizje, przypisania do celów), szyfrowanie kluczy API, triggery lokalności, seed z `.env`
- [x] 7.2 `LlmConfigStore` i bramka LLM z przeładowaniem bez restartu (api i workery), env jako górny limit polityki
- [x] 7.3 Kontrolowany ruch wychodzący do modeli chmurowych (proxy `llm-egress` z allowlistą hostów)
- [x] 7.4 API administratora `/admin/llm` (CRUD, test połączenia, sondy modeli, rewizje, aktywacja, rollback, audyt, metryki)
- [x] 7.5 Playground i porównanie modeli (bez retrievalu, syntetyczne zestawy promptów, limit zapytań)
- [x] 7.6 Interfejs `/admin/llm` (Dostawcy, Modele, Przypisania, Playground, Historia)
- [x] 7.7 Testy E2E Playwright, aktualizacja przeglądu bezpieczeństwa i dokumentacji

### Etap 8: Zarządzanie użytkownikami w panelu administratora (US-02)
*Dokumentacja: [docs/implementation/08_USER_MANAGEMENT.md](docs/implementation/08_USER_MANAGEMENT.md)*
- [x] 8.1 Klient Keycloak Admin API (konto serwisowe `spoldzielnia-admin` z minimalnymi uprawnieniami) i idempotentna konfiguracja realmu
- [x] 8.2 Model danych (źródło blokady, ostatnie logowanie), indeks sesji per użytkownik i zdarzenia audytu
- [x] 8.3 `UserAdminService`: tworzenie kont, reset hasła, blokada, wymuszenie zmiany hasła, role, zabezpieczenia (ostatni admin, step-up MFA)
- [x] 8.4 API administratora `/admin/users` (wyszukiwanie, akcje, limity, metryki)
- [x] 8.5 Interfejs `/admin/users` (lista, tworzenie konta, szczegóły użytkownika, akcje)
- [x] 8.6 Testy E2E Playwright, aktualizacja przeglądu bezpieczeństwa i dokumentacji

### Etap 9: Atlas wizualny — zdjęcia z oznaczeniami, weryfikacja ekspercka i rozpoznawanie obrazów (US-03)
*Dokumentacja: [docs/implementation/09_IMAGE_ATLAS.md](docs/implementation/09_IMAGE_ATLAS.md)*

*Faza A — zweryfikowany zbiór danych*
- [x] 9.1 Dziedziny i słownik etykiet (wiele dziedzin, synonimy, propozycje członków, zarządzanie przez ekspertów, przykładowa dziedzina tylko dla dev/testów)
- [x] 9.2 Prywatne oznaczenia zdjęć (prostokąty, opisy, RLS, szyfrowanie), bezpieczne serwowanie obrazów i podglądów
- [x] 9.3 Edytor oznaczeń w notatniku (mysz, dotyk, klawiatura)
- [x] 9.4 Przekazanie zdjęcia do atlasu (kopia bez metadanych, PII scrubbing, skan injection, flagi prywatności VLM, duplikaty)
- [x] 9.5 Weryfikacja ekspercka (korekty, kworum) i wspólny atlas `/atlas` z limitem pokrycia tarczy
- [x] 9.6 Zgody autora dla zdjęć atlasu (wstrzymaj, wznów, wycofaj, widoczność nazwiska, zgoda na eksport)
- [x] 9.7 Eksport zbioru danych (COCO/YOLO + manifest, domyślnie wyłączony, step-up MFA)

*Faza B — rozpoznawanie*
- [x] 9.8 Lokalny model embeddingów obrazu i indeks `atlas_visual_index` (outbox, proweniencja)
- [x] 9.9 „Rozpoznaj” w notatniku (wyszukiwanie podobnych oznaczeń + lokalny VLM, cytowania, sugestie oznaczeń)
- [x] 9.10 Testy E2E Playwright, aktualizacja przeglądu bezpieczeństwa i dokumentacji

### Etap 10: Wszystkie modele przez konfigurowalne API — bez wbudowanego LLM i modelu embeddingów (US-04)
*Dokumentacja: [docs/implementation/10_MODEL_PROVIDERS_API.md](docs/implementation/10_MODEL_PROVIDERS_API.md)*
- [x] 10.1 Model zaufania „dopuszczony do danych prywatnych” zamiast lokalności (lista operatora w `.env`, zgoda ADMIN z MFA, trigger, `private_data=True`, nowa reguła 4 w AGENTS.md)
- [x] 10.2 „Model nie jest skonfigurowany” zamiast awarii (`/api/capabilities`, zadania czekające na model, start bez modeli)
- [x] 10.3 Wersjonowane kolekcje Qdrant i bezpieczna zmiana modelu embeddingów (aliasy, ponowne embedowanie, zgody w obu wersjach)
- [x] 10.4 Transkrypcja mowy przez API (`/v1/audio/transcriptions`), wbudowany Whisper opcjonalny
- [x] 10.5 Dokumenty: Docling bez modeli ML, OCR przez API (VLM lub dedykowany OCR)
- [x] 10.6 Embeddingi obrazów przez API lub wbudowany SigLIP (opcjonalny), rozpoznawanie wyłączone bez modelu
- [x] 10.7 Compose bez serwera modeli domyślnie (profil `ollama`, relay do LM Studio, profil „tylko API”)
- [x] 10.8 Panel `/admin/llm`, strona „Gdzie przetwarzane są Twoje dane”, E2E, przegląd bezpieczeństwa i dokumentacja

### Etap 11: Usuwanie dostawców modeli w panelu administratora (US-05)
*Dokumentacja: [docs/implementation/11_PROVIDER_REMOVAL.md](docs/implementation/11_PROVIDER_REMOVAL.md)*
- [x] 11.1 Usuwanie dostawcy LLM (usunięcie albo archiwizacja, gdy historia rewizji go używa; blokada, gdy używa go aktywna konfiguracja, indeks lub szkic; migracja 0026, API `DELETE`, przycisk „Usuń”, testy i E2E)

### Etap 12: Układ w stylu Open Notebook i obsługa urządzeń mobilnych (US-06)
*Dokumentacja: [docs/implementation/12_APP_SHELL_RESPONSIVE.md](docs/implementation/12_APP_SHELL_RESPONSIVE.md)*
- [x] 12.1 Powłoka aplikacji: menu po lewej (sekcje, ikony, role, zwijanie do paska ikon zapamiętane w cookie), obszar roboczy po prawej, szuflada menu na telefonie
- [x] 12.2 Wspólne elementy interfejsu (`PageHeader`, `Tabs`, `DataTable`, `EmptyState`, `Skeleton`, okna dialogowe jako arkusz na telefonie, cele dotykowe 44px, `tailwind-merge`)
- [x] 12.3 Notatnik: lista notatek i obszar „Źródła | Notatka | Zapytaj” (kolumny na desktopie, zakładki na telefonie)
- [x] 12.4 Wiki, szkice i czat (kolumna do czytania, kompozytor przyklejony do dołu, przykładowe pytania)
- [x] 12.5 Atlas, kontrybucje, „Twoje dane”, strona główna i strony błędów
- [x] 12.6 Recenzje eksperckie (kolejka i szczegóły, jedno na raz na telefonie)
- [x] 12.7 Konsole administratora (`/admin/llm`, `/admin/users`, `/admin/security`, `/admin/atlas`)
- [x] 12.8 E2E na telefonie i tablecie (scenariusz 14), brak przewijania w poziomie przy 360px, axe, przegląd bezpieczeństwa i dokumentacja

### Etap 13: Wygląd organizacji — nazwa, logo i kolory (US-07)
*Dokumentacja: [docs/implementation/13_BRANDING.md](docs/implementation/13_BRANDING.md)*
- [x] 13.1 Model danych (migracja 0027, jeden wiersz, CHECK na kolory hex), wyprowadzanie palety z dwóch kolorów (OKLCH, kontrast WCAG AA, tryb ciemny), `BrandingService`, zdarzenia audytu
- [x] 13.2 Logo: tylko PNG/JPEG/WebP (SVG odrzucane), skan ClamAV, ponowne kodowanie Pillow, wersje logo i favicon, przechowywanie per wersja
- [x] 13.3 API: publiczne `GET /api/branding` i pliki logo/favicon (niezmienne adresy z wersją), zapis, podgląd, logo i reset dla ADMIN z MFA i CSRF
- [x] 13.4 Zastosowanie w aplikacji: `<style nonce>` z tokenami w layoucie (bez zmian CSP, bez mignięcia kolorów), tytuł i favicon, logo i nazwa w menu, pasku telefonu i na stronie głównej
- [x] 13.5 Strona `/admin/branding` („Wygląd”): nazwa, dwa kolory, podgląd jasny i ciemny z kontrastami, logo, przywracanie domyślnych
- [x] 13.6 E2E (scenariusz 15), przegląd bezpieczeństwa i dokumentacja

### Etap 14: Język interfejsu — polski i angielski (US-08)
*Dokumentacja: [docs/implementation/14_UI_LANGUAGE.md](docs/implementation/14_UI_LANGUAGE.md)*
- [x] 14.1 Język na koncie (migracja 0028 `users.locale`, `review_alert` jako kod), `GET /me` z językiem, `PUT /me/locale` (CSRF, limit, cookie `lang`), cookie ustawiane lub przejmowane przy logowaniu, katalog tekstów serwera (`spoldzielnia_ai/i18n`)
- [x] 14.2 Podstawy i18n we frontendzie (`next-intl` bez routingu, katalogi `pl`/`en`, `<html lang>`, formaty dat i liczb), przełącznik języka w menu, szufladzie i pasku ikon (zapis → cookie → przeładowanie), powłoka, strona główna i strony błędów
- [x] 14.3 Strony członka w katalogu: notatnik, wiki, czat, atlas, kontrybucje, „Twoje dane”, mapy kodów błędów, liczba mnoga ICU
- [x] 14.4 Recenzje i konsole administratora w katalogu; kontrola braku tekstów na sztywno (diakrytyki + ESLint `no-literal-string`)
- [x] 14.5 Odpowiedzi czatu po angielsku bez osłabienia ochrony przed destylacją (angielskie sloty znaku wodnego, wielojęzyczna kontrola dosłowności przez embeddingi, bez modelu embeddingów odpowiedź po polsku, angielskie frazy ekstrakcji i injection)
- [x] 14.6 E2E (scenariusz 16), przegląd bezpieczeństwa i dokumentacja

---
### Etap 15: Monitoring zużycia AI — tokeny i szacunkowy koszt (US-09)
*Dokumentacja: [docs/implementation/15_AI_USAGE_MONITORING.md](docs/implementation/15_AI_USAGE_MONITORING.md)*
- [ ] 15.1 Migracja `llm_usage_daily` (RLS, CHECK, unikalny klucz), ceny modelu w konfiguracji (migracja + rewizja), wpisy w `test_db_models.py`
- [ ] 15.2 Serwis zużycia: atomowy upsert liczników po każdym wywołaniu w bramce LLM; test: liczenie, wiersz „system”, model bez ceny, błąd zapisu nie przerywa odpowiedzi
- [ ] 15.3 Koszt w mikrojednostkach całkowitych i endpoint `GET /me/usage` z testami RLS (użytkownik nie widzi cudzych danych)
- [ ] 15.4 Endpoint administratora `GET /admin/usage` (per użytkownik, wiersz systemowy, sumy, podział na cel i model); test integracyjny
- [ ] 15.5 Frontend: „Zużycie AI” w „Twoje dane” i w konsoli administratora; stany puste/bez ceny/błędu; teksty pl+en; Vitest; scenariusz E2E
- [ ] 15.6 Przegląd bezpieczeństwa, sekcje etapu, US-09 DONE z hashami commitów

