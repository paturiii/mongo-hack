import type { Metadata } from "next";
import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";
import "@astryxdesign/theme-neutral/theme.css";
import "@astryxdesign/theme-neutral/fonts.css";
import "../theme/wander.css";

export const metadata: Metadata = {
  title: "Wander · Your trip, looked after",
  description: "Review travel updates and choose a fix for your Lisbon trip.",
};
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
