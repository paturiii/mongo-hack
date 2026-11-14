import type {Metadata, Viewport} from 'next';
import '@astryxdesign/core/reset.css';
import '@astryxdesign/core/astryx.css';
import {Providers} from './providers';

export const metadata: Metadata = {
  title: 'Wander',
  description: 'Asks the right questions, reads what travelers say, hands you one board of booking links.',
};

export const viewport: Viewport = {width: 'device-width', initialScale: 1, interactiveWidget: 'resizes-content'};

export default function RootLayout({children}: {children: React.ReactNode}) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
