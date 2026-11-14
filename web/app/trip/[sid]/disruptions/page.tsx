import { TripBoard } from "@/components/wander/TripBoard";

// Track 1: disruptions for a trip. The segment is [sid] to share the /trip/[sid] route; its value is the trip id.
export default async function Page({ params }: { params: Promise<{ sid: string }> }) {
  const { sid } = await params;
  return <TripBoard tripId={sid} disruptionsOnly />;
}
