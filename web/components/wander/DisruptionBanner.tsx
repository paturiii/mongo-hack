'use client';

import {VStack} from '@astryxdesign/core/Layout';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import type {Alert} from '@/lib/api';

/**
 * Slot for Track 1 (disruption management). Renders board-channel `alerts` live; Track 1 owns this component
 * and the /trip/[id]/disruptions page and can extend it with option buttons.
 */
export function DisruptionBanner({alerts}: {alerts: Alert[]}) {
  if (!alerts.length) return null;
  return (
    <VStack gap={2}>
      {alerts.slice(0, 3).map(a => (
        <Banner
          key={a._id}
          status="warning"
          title={a.title}
          description={a.body}
          isDismissable
          endContent={a.audio_url ? <Button label="Play briefing" size="sm" onClick={() => new Audio(a.audio_url!).play()} /> : undefined}
        />
      ))}
    </VStack>
  );
}
