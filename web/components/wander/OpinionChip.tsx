'use client';

import {Token} from '@astryxdesign/core/Token';

const SOURCE: Record<string, {label: string; color: 'orange' | 'blue' | 'purple' | 'green' | 'red' | 'gray'}> = {
  reddit: {label: 'Reddit', color: 'orange'},
  forum: {label: 'Forum', color: 'blue'},
  blog: {label: 'Blog', color: 'purple'},
  review: {label: 'Reviews', color: 'green'},
  video: {label: 'Video', color: 'red'},
  news: {label: 'News', color: 'gray'},
};

/** A sourced traveler opinion: short quote + where it came from, linked to the thread. */
export function OpinionChip({text, sourceType, url}: {text: string; sourceType: string; url: string}) {
  const s = SOURCE[sourceType] ?? {label: sourceType, color: 'gray' as const};
  return <Token size="sm" color={s.color} label={`${s.label}: “${text}”`} href={url} description={`Open the ${s.label} source`} />;
}
