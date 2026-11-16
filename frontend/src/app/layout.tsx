import type { Metadata, Viewport } from "next";
import { Inter, Outfit } from "next/font/google";
import { cookies, headers } from "next/headers";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getTranslations } from "next-intl/server";

import { ProcessorsBanner } from "@/components/privacy/ProcessorsBanner";
import { AppShell } from "@/components/shell/AppShell";
import { ToastProvider } from "@/components/ui/Toast";
import { getBranding } from "@/lib/branding";
import { BrandingProvider } from "@/lib/branding-context";
import { brandingCss } from "@/lib/branding-css";
import { NonceProvider } from "@/lib/nonce";
import { QueryProvider } from "@/lib/query";
import { isCollapsed, SIDEBAR_COOKIE } from "@/lib/sidebar";

import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin", "latin-ext"] });
const outfit = Outfit({ variable: "--font-outfit", subsets: ["latin", "latin-ext"] });

/** The tab title and icons follow the organisation's branding (13.4). */
export async function generateMetadata(): Promise<Metadata> {
  const branding = await getBranding();
  const t = await getTranslations("common");
  return {
    title: { default: branding.org_name, template: `%s · ${branding.org_name}` },
    description: t("description"),
    icons: branding.favicon_urls
      ? { icon: [{ url: branding.favicon_urls["32"], sizes: "32x32", type: "image/png" }], apple: branding.favicon_urls["180"] }
      : undefined,
  };
}

// `cover` lets the shell pad for notches and the home indicator (env(safe-area-inset-*)).
// `colorScheme` emits <meta name="color-scheme">: native controls and the first paint follow the theme.
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", colorScheme: "light dark" };

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // Reading the request headers makes every page render per request: the CSP nonce from
  // src/proxy.ts differs on each one (Next.js applies it to its scripts automatically).
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  const collapsed = isCollapsed((await cookies()).get(SIDEBAR_COOKIE)?.value);
  const branding = await getBranding();
  // Only validated #rrggbb values reach this CSS (rule R2); the CSP allows it by its nonce.
  const css = brandingCss(branding.palette);
  // The `lang` cookie's language (14.2): the server renders it, so the first paint is right.
  const locale = await getLocale();
  const t = await getTranslations("common");
  return (
    <html lang={locale} className={`${inter.variable} ${outfit.variable} h-full antialiased`}>
      <head>{css && <style nonce={nonce} id="branding">{css}</style>}</head>
      <body className="min-h-full font-sans">
        <a
          href="#content"
          className="sr-only z-50 rounded-lg bg-surface px-4 py-2 font-medium focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
        >
          {t("skipToContent")}
        </a>
        <NextIntlClientProvider>
          <NonceProvider nonce={nonce}>
            <QueryProvider>
              <ToastProvider>
                <BrandingProvider branding={branding}>
                  <AppShell initialCollapsed={collapsed} banner={<ProcessorsBanner />}>
                    {children}
                  </AppShell>
                </BrandingProvider>
              </ToastProvider>
            </QueryProvider>
          </NonceProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
