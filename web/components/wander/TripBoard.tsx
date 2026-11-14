"use client";
import { useCallback, useEffect, useState } from "react";
import { Theme } from "@astryxdesign/core/theme";
import { neutralTheme } from "@astryxdesign/theme-neutral/built";
import { Button } from "@astryxdesign/core/Button";
import { Banner } from "@astryxdesign/core/Banner";
import { Card } from "@astryxdesign/core/Card";
import { Heading } from "@astryxdesign/core/Heading";
import { HStack } from "@astryxdesign/core/HStack";
import { VStack } from "@astryxdesign/core/VStack";
import { Text } from "@astryxdesign/core/Text";
import { Link } from "@astryxdesign/core/Link";
import { DisruptionBanner } from "./DisruptionBanner";
import type { Snapshot } from "./types";

export function TripBoard({ slug, tripId, disruptionsOnly = false }: { slug?: string; tripId?: string; disruptionsOnly?: boolean }) {
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [dark, setDark] = useState(false);
  const [connected, setConnected] = useState(false);
  const [signedIn, setSignedIn] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [day, setDay] = useState(0);
  const [resolvedSlug, setResolvedSlug] = useState(slug || "");
  const refresh = useCallback(async () => {
    if (!resolvedSlug) return;
    try {
      const response = await fetch(`/api/boards/${encodeURIComponent(resolvedSlug)}`);
      if (!response.ok) throw new Error(response.status === 404 ? "This board could not be found." : "Could not load your board.");
      setData(await response.json()); setError("");
    } catch (err) { setError(err instanceof Error ? err.message : "Could not load your board."); }
  }, [resolvedSlug]);
  useEffect(() => { setDark(window.matchMedia("(prefers-color-scheme: dark)").matches); }, []);
  useEffect(() => {
    if (!tripId) return;
    fetch(`/api/trips/${encodeURIComponent(tripId)}/board`).then(async response => {
      if (!response.ok) throw new Error("Sign in through your trip board to view these updates.");
      setResolvedSlug((await response.json()).share_slug);
    }).catch(err => setError(err.message));
  }, [tripId]);
  useEffect(() => {
    refresh();
    if (!resolvedSlug) return;
    const stream = new EventSource(`/api/boards/${encodeURIComponent(resolvedSlug)}/events`);
    stream.addEventListener("board", event => {
      setData(JSON.parse((event as MessageEvent).data)); setConnected(true); setError("");
    });
    stream.onerror = () => setConnected(false);
    return () => stream.close();
  }, [resolvedSlug, refresh]);
  async function signInDemo() {
    setSigningIn(true);
    try {
      const response = await fetch("/api/demo/session", { method: "POST" });
      if (!response.ok) throw new Error("Demo sign-in is unavailable. Sign in through the main app.");
      setSignedIn(true); setError("");
    } catch (err) { setError(err instanceof Error ? err.message : "Sign-in failed."); }
    finally { setSigningIn(false); }
  }
  const open = data?.disruptions.filter(d => ["open", "notified"].includes(d.status)) || [];
  const history = data?.disruptions.filter(d => ["resolved", "dismissed"].includes(d.status)) || [];
  const currentDay = data?.board.days[Math.min(day, data.board.days.length - 1)];
  return <Theme theme={neutralTheme} mode={dark ? "dark" : "light"}>
    <main className="wander-page">
      <VStack gap={6}>
        <HStack gap={3} hAlign="between" wrap="wrap">
          <Link href="/" color="primary"><Heading level={2}>Wander</Heading></Link>
          <HStack gap={2} wrap="wrap"><Text type="supporting">{connected ? "Live updates connected" : "Connecting to live updates…"}</Text>
            <Button label={dark ? "Light mode" : "Dark mode"} variant="ghost" onClick={() => setDark(!dark)} /></HStack>
        </HStack>
        <VStack gap={2}><Text type="label" color="secondary">YOUR TRIP, LOOKED AFTER</Text>
          <Heading level={1}>{disruptionsOnly ? "Trip updates" : data?.board.title || "Your trip board"}</Heading>
          <Text color="secondary">{data?.board.summary || "Loading your trip…"}</Text>
          {data && <Text type="supporting">Board version {data.board.version} · {open.length} open updates{data.board.fixture_replan ? " · Demo replan applied" : ""}</Text>}
        </VStack>
        {error && <Banner status="error" title="Something needs attention" description={error} endContent={<Button label="Retry" onClick={refresh} />} />}
        {data?.board.fixture && <Banner status="info" title="Lisbon demo trip" description="These disruptions and bookings are simulated. Choose a fix to see a scoped demo replan."
          endContent={<Button label={signedIn ? "Demo traveler signed in" : "Continue as demo traveler"} isDisabled={signedIn} isLoading={signingIn} onClick={signInDemo} />} />}
        <section aria-label="Travel updates"><VStack gap={3}>
          <Heading level={2}>Travel updates</Heading>
          {open.length === 0 && data && <Card><Text>{history.length ? "All caught up. Your confirmed choices are saved below." : "No disruptions to report. We’re watching your trip."}</Text></Card>}
          {open.map(disruption => <DisruptionBanner key={disruption._id} disruption={disruption}
            alerts={data!.alerts.filter(a => a.disruption_id === disruption._id)} onChanged={refresh} />)}
          {data?.alerts.filter(a => a.disruption_id.startsWith("notice_") && a.source_url && a.fetched_at).map(a =>
            <Banner key={a._id} status="info" title={a.title} description={a.body}
              endContent={<Link href={a.source_url} isExternalLink>Check price</Link>} />)}
        </VStack></section>
        {!disruptionsOnly && currentDay && <section aria-label="Day by day"><VStack gap={3}>
          <Heading level={2}>Your days in Lisbon</Heading>
          <HStack gap={2} wrap="wrap">{data?.board.days.map((d, index) => <Button key={d.date}
            label={new Date(d.date + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" })}
            variant={day === index ? "primary" : "secondary"} onClick={() => setDay(index)} aria-pressed={day === index} />)}</HStack>
          <Card><VStack gap={4}><Heading level={3}>{currentDay.theme}</Heading>
            {currentDay.slots.map(slot => <HStack key={slot.slot_id} gap={4} vAlign="start" className="wander-slot">
              <Text type="label">{slot.time}</Text><VStack gap={1}><Heading level={4}>{slot.item.name}</Heading>
                <Text color="secondary">{slot.item.why}</Text>
                {slot.item.source_url && <Link href={slot.item.source_url} isExternalLink>Place source</Link>}
              </VStack></HStack>)}
          </VStack></Card>
        </VStack></section>}
        {history.length > 0 && <section aria-label="Confirmed choices"><VStack gap={3}>
          <Heading level={2}>Confirmed choices</Heading>
          {history.map(item => <Card key={item._id}><Text>{item.kind.replaceAll("_", " ")} · {item.status}</Text></Card>)}
        </VStack></section>}
        <Text type="supporting" color="secondary">Wander suggests options. You choose what changes. Book directly with providers.</Text>
      </VStack>
    </main>
  </Theme>;
}
