import { TripBoard } from "@/components/wander/TripBoard";

// Track 1: full disruption view for a board (open updates, fixes, history, voice briefings).
export default async function Page({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <TripBoard slug={slug} />;
}
