'use client';

import {useCallback, useEffect, useState} from 'react';
import {Layout, LayoutContent, VStack, HStack} from '@astryxdesign/core/Layout';
import {Text, Heading} from '@astryxdesign/core/Text';
import {List, ListItem} from '@astryxdesign/core/List';
import {Button} from '@astryxdesign/core/Button';
import {TextArea} from '@astryxdesign/core/TextArea';
import {Card} from '@astryxdesign/core/Card';
import {Badge} from '@astryxdesign/core/Badge';
import {Banner} from '@astryxdesign/core/Banner';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Skeleton} from '@astryxdesign/core/Skeleton';
import {Timestamp} from '@astryxdesign/core/Timestamp';
import {AppFrame} from '@/components/wander/AppFrame';
import {api, type Memory} from '@/lib/api';
import {useMe} from '@/lib/useMe';

const LABEL: Record<string, string> = {
  budget_band: 'Budget',
  pace: 'Pace',
  stay_vibe: 'Stay vibe',
  nightlife_intensity: 'Nights out',
  dietary: 'Diet',
  mobility: 'Mobility',
  stay_type: 'Stay type',
  booking_ahead: 'Booking style',
  transport_pref: 'Transport',
  dealbreakers: 'Avoid',
  origin: 'Home city',
};
const SOURCE: Record<string, string> = {answer: 'from your answers', refine: 'from a change you asked for', rating: 'from a past trip', disruption_choice: 'from a disruption fix', manual: 'added by you'};

export default function MemoryPage() {
  const {me} = useMe();
  const [items, setItems] = useState<Memory[] | null>(null);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  const load = useCallback(() => api<Memory[]>('/api/memory').then(setItems).catch(e => setError(e.message)), []);
  useEffect(() => {
    if (me) load();
  }, [me, load]);

  const forget = async (m: Memory) => {
    setItems(xs => xs?.filter(x => x._id !== m._id) ?? null);
    await api(`/api/memory/${m._id}`, {method: 'DELETE'}).catch(e => {
      setError(e.message);
      load();
    });
  };

  const add = async () => {
    if (!text.trim()) return;
    setIsSaving(true);
    try {
      await api('/api/memory', {body: {text}});
      setText('');
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setIsSaving(false);
    }
  };

  const prefs = items?.filter(m => m.kind === 'pref') ?? [];
  const notes = items?.filter(m => m.kind !== 'pref') ?? [];
  const fmt = (v: unknown) => (Array.isArray(v) ? v.join(', ') : String(v ?? '')).replace(/_/g, ' ');

  return (
    <AppFrame me={me}>
      <Layout
        height="fill"
        contentWidth={720}
        padding={6}
        content={
          <LayoutContent>
            <VStack gap={6}>
              <VStack gap={1}>
                <Heading level={1}>What Wander remembers</Heading>
                <Text type="body" color="secondary">
                  These shape your next trip, so I ask fewer questions. Forget anything and I'll ask again.
                </Text>
              </VStack>
              {error && <Banner status="error" title={error} isDismissable onDismiss={() => setError('')} />}

              <VStack gap={2}>
                <Heading level={3}>Preferences</Heading>
                {items === null ? (
                  <Skeleton height={120} />
                ) : prefs.length === 0 ? (
                  <EmptyState title="No preferences yet" description="Answer a trip's questions and they'll show up here." isCompact />
                ) : (
                  <List hasDividers>
                    {prefs.map(m => (
                      <ListItem
                        key={m._id}
                        label={`${LABEL[m.key ?? ''] ?? m.key}: ${fmt(m.value)}`}
                        description={SOURCE[m.source ?? ''] ?? m.source}
                        endContent={<Button label="Forget" size="sm" variant="ghost" onClick={() => forget(m)} />}
                      />
                    ))}
                  </List>
                )}
              </VStack>

              <VStack gap={2}>
                <Heading level={3}>Past trips & notes</Heading>
                {items !== null && notes.length === 0 && <Text type="supporting">Nothing yet. Tell me how a trip went and I'll use it next time.</Text>}
                {notes.length > 0 && (
                  <List hasDividers>
                    {notes.map(m => (
                      <ListItem
                        key={m._id}
                        label={m.text ?? ''}
                        description={
                          <HStack gap={2} vAlign="center">
                            <Badge label={m.kind === 'episode' ? 'Trip' : 'Feedback'} variant={m.kind === 'episode' ? 'info' : 'neutral'} />
                            <Timestamp value={m.updated_at} format="date" />
                          </HStack>
                        }
                        endContent={<Button label="Forget" size="sm" variant="ghost" onClick={() => forget(m)} />}
                      />
                    ))}
                  </List>
                )}
              </VStack>

              <Card padding={5}>
                <VStack gap={3}>
                  <TextArea label="Tell Wander something" description="e.g. “Porto 2026: loved the tascas, hated early starts”" value={text} onChange={setText} rows={3} />
                  <HStack hAlign="end">
                    <Button label="Remember this" variant="primary" isLoading={isSaving} onClick={add} />
                  </HStack>
                </VStack>
              </Card>
            </VStack>
          </LayoutContent>
        }
      />
    </AppFrame>
  );
}
