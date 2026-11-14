'use client';

import NextLink from 'next/link';
import {Theme} from '@astryxdesign/core/theme';
import {LinkProvider} from '@astryxdesign/core/Link';
import {themes, defaultThemeSlug} from '../astryx-themes';

export function Providers({children}: {children: React.ReactNode}) {
  return (
    <Theme theme={themes[defaultThemeSlug as keyof typeof themes]}>
      <LinkProvider component={NextLink}>{children}</LinkProvider>
    </Theme>
  );
}
