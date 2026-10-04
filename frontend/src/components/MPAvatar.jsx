import React, { useEffect, useState } from 'react';

// Resolves a real, publicly-sourced photograph for an MP (never AI-generated)
// and falls back cleanly to an initials avatar when none can be found.
//
// Sources tried in order, client-side, via public CORS-enabled APIs
// (`origin=*`): English Wikipedia, Wikidata (property P18), Hindi Wikipedia.
// These pull from Wikimedia Commons, which for sitting Indian MPs is largely
// built from official Sansad/PIB photo uploads — real, public/official
// images, never AI-generated. Stacking three sources meaningfully improves
// coverage past English Wikipedia alone, which only has dedicated articles
// for higher-profile MPs; Wikidata and Hindi Wikipedia catch a good share of
// the rest. Results are cached for the session so re-opening the same MP's
// profile, or re-running a comparison, doesn't re-query.

const memoryCache = new Map();
const SESSION_CACHE_KEY = 'mplads_mp_photo_cache_v1';

function readSessionCache() {
  try {
    const raw = sessionStorage.getItem(SESSION_CACHE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function writeSessionCache(cache) {
  try {
    sessionStorage.setItem(SESSION_CACHE_KEY, JSON.stringify(cache));
  } catch {
    // Ignore quota / privacy-mode storage errors — caching is best-effort.
  }
}

async function queryWikipediaImage(host, name, extraTerms) {
  const searchUrl = `https://${host}/w/api.php?action=query&list=search&srsearch=${encodeURIComponent(
    `${name} ${extraTerms}`
  )}&format=json&origin=*&srlimit=1`;
  const searchRes = await fetch(searchUrl);
  const searchData = await searchRes.json();
  const title = searchData?.query?.search?.[0]?.title;
  if (!title) return null;

  const imgUrl = `https://${host}/w/api.php?action=query&titles=${encodeURIComponent(
    title
  )}&prop=pageimages&piprop=original&format=json&origin=*`;
  const imgRes = await fetch(imgUrl);
  const imgData = await imgRes.json();
  const page = Object.values(imgData?.query?.pages || {})[0];
  return page?.original?.source || null;
}

// Wikidata often carries an official parliamentary photo (property P18) even
// for MPs whose Wikipedia article is a short stub with no infobox image, so
// it catches a meaningful slice of names Wikipedia search alone misses.
async function queryWikidataImage(name) {
  const searchUrl = `https://www.wikidata.org/w/api.php?action=wbsearchentities&search=${encodeURIComponent(
    name
  )}&language=en&type=item&limit=3&format=json&origin=*`;
  const searchRes = await fetch(searchUrl);
  const searchData = await searchRes.json();
  const candidates = searchData?.search || [];
  // Prefer a candidate whose description reads like a politician/MP entry.
  const candidate =
    candidates.find((c) => /member of parliament|politician|mla|lok sabha|rajya sabha/i.test(c.description || '')) ||
    candidates[0];
  if (!candidate?.id) return null;

  const claimUrl = `https://www.wikidata.org/w/api.php?action=wbgetclaims&entity=${candidate.id}&property=P18&format=json&origin=*`;
  const claimRes = await fetch(claimUrl);
  const claimData = await claimRes.json();
  const filename = claimData?.claims?.P18?.[0]?.mainsnak?.datavalue?.value;
  if (!filename) return null;

  return `https://commons.wikimedia.org/wiki/Special:FilePath/${encodeURIComponent(filename)}`;
}

async function lookupOfficialPhoto(name) {
  if (memoryCache.has(name)) return memoryCache.get(name);

  const sessionCache = readSessionCache();
  if (Object.prototype.hasOwnProperty.call(sessionCache, name)) {
    memoryCache.set(name, sessionCache[name]);
    return sessionCache[name];
  }

  let result = null;
  // Try, in order: English Wikipedia -> Wikidata (P18) -> Hindi Wikipedia.
  // Each is a real public/official-photo source (Wikimedia Commons, largely
  // sourced from Sansad/PIB uploads for sitting MPs) — never AI-generated —
  // and stacking them meaningfully lifts coverage past English Wikipedia
  // alone, which tends to only have dedicated articles for higher-profile MPs.
  const attempts = [
    () => queryWikipediaImage('en.wikipedia.org', name, 'Indian politician Member of Parliament Lok Sabha'),
    () => queryWikidataImage(name),
    () => queryWikipediaImage('hi.wikipedia.org', name, 'भारतीय राजनीतिज्ञ सांसद लोक सभा'),
  ];

  for (const attempt of attempts) {
    try {
      // eslint-disable-next-line no-await-in-loop
      const url = await attempt();
      if (url) {
        result = url;
        break;
      }
    } catch {
      // Move on to the next source.
    }
  }

  memoryCache.set(name, result);
  sessionCache[name] = result;
  writeSessionCache(sessionCache);
  return result;
}

/**
 * @param {string} name        Raw (untranslated) MP name — used for photo lookup.
 * @param {string} initials    Precomputed initials shown as fallback.
 * @param {number} size        Diameter in px.
 * @param {boolean} enablePhoto  When false (e.g. constituency profiles, which
 *                                represent an area rather than a person), skip
 *                                the lookup and always show initials.
 */
export default function MPAvatar({ name, initials, size = 46, enablePhoto = true }) {
  const [photoUrl, setPhotoUrl] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setPhotoUrl(null);
    setFailed(false);
    if (!enablePhoto || !name) return undefined;
    lookupOfficialPhoto(name).then((url) => {
      if (!cancelled) setPhotoUrl(url);
    });
    return () => {
      cancelled = true;
    };
  }, [name, enablePhoto]);

  const showPhoto = enablePhoto && !!photoUrl && !failed;

  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        flexShrink: 0,
        overflow: 'hidden',
        background: 'var(--shell-900)',
        border: '1.5px solid var(--seal-gold)',
        boxShadow: '0 2px 10px rgba(20, 28, 51, 0.18)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {showPhoto ? (
        <img
          src={photoUrl}
          alt={name}
          onError={() => setFailed(true)}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        />
      ) : (
        <span
          style={{
            color: 'var(--seal-gold-bright)',
            fontFamily: 'var(--font-display)',
            fontWeight: 600,
            fontSize: Math.round(size * 0.35),
            lineHeight: 1,
          }}
        >
          {initials}
        </span>
      )}
    </div>
  );
}
