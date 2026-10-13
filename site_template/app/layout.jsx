import { loadSiteConfig, fact } from "../lib/site-config";
import "./globals.css";

export const dynamic = "force-dynamic";

export function generateMetadata() {
  const config = loadSiteConfig();
  return {
    title: config.seo_title || fact(config.business_name) || "Preview",
    description: config.seo_description || "",
    robots: config.noindex ? { index: false, follow: false } : undefined,
  };
}

export default function RootLayout({ children }) {
  const config = loadSiteConfig();
  const accent = config.primary_color || "#2f6f5e";
  return (
    <html lang="pt">
      <body style={{ "--accent": accent }}>{children}</body>
    </html>
  );
}
