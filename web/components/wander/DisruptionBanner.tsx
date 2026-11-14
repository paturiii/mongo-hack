"use client";
import { useState } from "react";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { HStack } from "@astryxdesign/core/HStack";
import { VStack } from "@astryxdesign/core/VStack";
import { Link } from "@astryxdesign/core/Link";
import { Text } from "@astryxdesign/core/Text";
import type { Alert, Disruption } from "./types";

export function DisruptionBanner({ disruption, alerts, onChanged }: {
  disruption: Disruption; alerts: Alert[]; onChanged: () => void;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const voice = alerts.find(a => a.channel === "voice" && a.audio_url);
  async function choose(optionId: string) {
    setPending(optionId); setMessage("");
    try {
      const response = await fetch(`/api/disruptions/${encodeURIComponent(disruption._id)}/resolve`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ option_id: optionId }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not confirm this choice. Try again.");
      setMessage(data.refine_request_id ? "Choice confirmed. Your affected plans are being updated." : "Choice confirmed. Your plan is unchanged.");
      onChanged();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not confirm this choice."); }
    finally { setPending(null); }
  }
  const title = `${disruption.fixture ? "Demo · " : ""}${disruption.kind.replaceAll("_", " ")} · severity ${disruption.severity}/5`;
  return <Banner status={disruption.severity >= 4 ? "error" : "warning"} title={title}
    description={disruption.evidence[0].summary} collapsible={false}>
    <VStack gap={3}>
      <Text>Affects {disruption.affected.days.join(", ")} · {disruption.affected.slot_ids.length} activities. Choose a fix to confirm any changes.</Text>
      <HStack gap={2} wrap="wrap">
        {disruption.options.map((option, index) => <Button key={option.id} label={option.label}
          variant={index === 0 ? "primary" : "secondary"} isLoading={pending === option.id}
          isDisabled={pending !== null && pending !== option.id} onClick={() => choose(option.id)} />)}
      </HStack>
      <HStack gap={3} wrap="wrap">
        {disruption.options.flatMap(option => option.actions.filter(a => a.type === "link" && a.url).map(a => a.url!))
          .filter((url, index, all) => all.indexOf(url) === index).map(url => <Link key={url} href={url} isExternalLink>Explore alternatives</Link>)}
        {disruption.evidence.map(source => <Link key={source.source_url} href={source.source_url} isExternalLink>
          {source.source_type} source · {new Date(source.fetched_at).toLocaleDateString()}
        </Link>)}
      </HStack>
      {voice && <VStack gap={1}><Text type="supporting">{voice.delivery_mode === "fixture" ? "Recorded demo briefing" : "Your voice briefing"}</Text>
        <audio controls preload="none" src={voice.audio_url} aria-label="Disruption voice briefing" /></VStack>}
      <Text type="supporting">{alerts.filter(a => a.channel !== "board").map(a => `${a.channel}: ${a.last_error ? "delivery needs attention" : a.delivery_mode === "fixture" ? "demo recorded" : a.status}`).join(" · ")}</Text>
      <div role="status" aria-live="polite">{message && <Text>{message}</Text>}</div>
    </VStack>
  </Banner>;
}
