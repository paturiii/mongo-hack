export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const backend = process.env.API_BASE_URL || "http://127.0.0.1:8000";
  const upstream = await fetch(`${backend}/api/boards/${encodeURIComponent(slug)}/events`, {
    cache: "no-store", signal: request.signal,
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "Content-Encoding": "none",
      "X-Accel-Buffering": "no",
    },
  });
}
