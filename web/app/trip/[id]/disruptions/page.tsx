import { TripBoard } from "@/components/wander/TripBoard";
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <TripBoard tripId={id} disruptionsOnly />;
}
