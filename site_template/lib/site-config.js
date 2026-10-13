import sample from "../sample-config.json";

/**
 * SiteConfig contract (backend app/core/schemas.py -> SiteConfig):
 * facts are { value, source_url, confidence } and may be null — a missing
 * fact means "omit the section", never invent content.
 *
 * MUST be called at request time (force-dynamic pages). Reading SITE_CONFIG
 * at module top-level would bake one lead's content into a static build.
 */
export function loadSiteConfig() {
  if (process.env.SITE_CONFIG) {
    return JSON.parse(process.env.SITE_CONFIG);
  }
  return sample;
}

/** Safe accessor: returns the fact value or null when the fact is absent. */
export function fact(sourcedFact) {
  return sourcedFact && sourcedFact.value ? sourcedFact.value : null;
}

export function whatsappLink(phoneFact) {
  const raw = fact(phoneFact);
  if (!raw) return null;
  return `https://wa.me/${raw.replace(/\D/g, "")}`;
}
