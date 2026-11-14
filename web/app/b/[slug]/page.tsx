import { TripBoard } from "@/components/wander/TripBoard";
export default async function Page({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <TripBoard slug={slug} />;
}
