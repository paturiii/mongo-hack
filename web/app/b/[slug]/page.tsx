'use client';

import {use, useEffect, useMemo, useState} from 'react';
import {Layout, LayoutContent, VStack, HStack} from '@astryxdesign/core/Layout';
import {Text, Heading} from '@astryxdesign/core/Text';
import {TabList, Tab} from '@astryxdesign/core/TabList';
import {List, ListItem} from '@astryxdesign/core/List';
import {Card} from '@astryxdesign/core/Card';
import {Grid} from '@astryxdesign/core/Grid';
import {Badge} from '@astryxdesign/core/Badge';
import {Button} from '@astryxdesign/core/Button';
import {Link} from '@astryxdesign/core/Link';
import {Switch} from '@astryxdesign/core/Switch';
import {ProgressBar} from '@astryxdesign/core/ProgressBar';
import {StatusDot} from '@astryxdesign/core/StatusDot';
import {Banner} from '@astryxdesign/core/Banner';
import {Skeleton} from '@astryxdesign/core/Skeleton';
import {Divider} from '@astryxdesign/core/Divider';
import {AppFrame} from '@/components/wander/AppFrame';
import {OpinionChip} from '@/components/wander/OpinionChip';
import {TripMap} from '@/components/wander/TripMap';
import {DisruptionBanner} from '@/components/wander/DisruptionBanner';
import {api, GATEWAY, type BoardView, type Booking} from '@/lib/api';
import {useMe} from '@/lib/useMe';

const GROUPS: {title: string; kinds: string[]}[] = [
  {title: 'Getting there', kinds: ['flight', 'transfer']},
  {title: 'Stay', kinds: ['stay']},
  {title: 'Getting around', kinds: ['train', 'bus', 'rebook']},
  {title: 'Tickets', kinds: ['monument_ticket', 'tour']},
  {title: 'Nights out', kinds: ['club_ticket']},
  {title: 'Eat', kinds: ['restaurant']},
];

export default function BoardPage({params}: {params: Promise<{slug: string}>}) {
  const {slug} = use(params);
  const {me} = useMe({optional: true});
  const [view, setView] = useState<BoardView | null>(null);
  const [error, setError] = useState('');
  const [day, setDay] = useState('0');
  const [isLive, setIsLive] = useState(false);

  // Live board: MongoDB change stream → SSE (straight to the gateway).
  useEffect(() => {
    api<BoardView>(`/api/boards/${slug}`).then(setView).catch(e => setError(e.message));
    const es = new EventSource(`${GATEWAY}/api/boards/${slug}/events`);
    es.addEventListener('board', e => setView(JSON.parse((e as MessageEvent).data)));
    es.onopen = () => setIsLive(true);
    es.onerror = () => setIsLive(false);
    return () => es.close();
  }, [slug]);

  const isOwner = !!me && me.id === view?.board.user_id;
  const b = view?.board;
  const current = b?.days[Number(day)] ?? b?.days[0];

  const toggle = async (bk: Booking, booked: boolean) => {
    try {
      setView(await api<BoardView>(`/api/boards/${slug}/bookings/${bk.link_id}`, {body: {booked}}));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const copyText = () => {
    if (!b) return;
    const lines = [b.title, '', ...b.days.flatMap((d, i) => [`Day ${i + 1} · ${d.date.slice(0, 10)} · ${d.theme ?? ''}`, ...d.slots.map(s => `  ${s.time}  ${s.item.name}`), ''])];
    lines.push('Bookings:', ...b.bookings.map(bk => `  [${bk.status === 'user_marked_booked' ? 'x' : ' '}] ${bk.label} — ${bk.url}`));
    navigator.clipboard.writeText(lines.join('\n'));
  };

  const grouped = useMemo(
    () => GROUPS.map(g => ({...g, items: (b?.bookings ?? []).filter(bk => g.kinds.includes(bk.kind))})).filter(g => g.items.length),
    [b],
  );

  return (
    <AppFrame me={me}>
      <Layout
        height="fill"
        contentWidth={1200}
        padding={6}
        content={
          <LayoutContent>
            {error && <Banner status="error" title={error} isDismissable onDismiss={() => setError('')} />}
            {!b ? (
              <VStack gap={4}>
                <Skeleton height={48} />
                <Skeleton height={360} />
              </VStack>
            ) : (
              <VStack gap={6}>
                <VStack gap={2}>
                  <HStack gap={2} vAlign="center" wrap="wrap">
                    <StatusDot
                      variant={isLive ? 'success' : 'neutral'}
                      isPulsing={isLive}
                      label={isLive ? 'Live' : 'Offline'}
                      tooltip={isLive ? 'Live: updates as soon as the plan changes' : 'Reconnecting…'}
                    />
                    <Text type="supporting">{isLive ? 'Live' : 'Offline'}</Text>
                    <Badge label={`v${b.version}`} variant="neutral" />
                  </HStack>
                  <Heading level={1}>{b.title}</Heading>
                  {b.summary && <Text type="body" color="secondary">{b.summary}</Text>}
                  <HStack gap={2} wrap="wrap">
                    <Button label="Add to calendar (.ics)" size="sm" href={`/api/boards/${slug}/ics`} />
                    <Button label="Copy as text" size="sm" onClick={copyText} />
                    <Button label="Print" size="sm" variant="ghost" onClick={() => window.print()} />
                  </HStack>
                </VStack>

                <DisruptionBanner alerts={view!.alerts} />

                <Grid columns={{minWidth: 340, max: 2}} gap={6}>
                  <VStack gap={4}>
                    <TabList value={day} onChange={setDay} hasDivider>
                      {b.days.map((d, i) => (
                        <Tab key={i} value={String(i)} label={`Day ${i + 1}`} />
                      ))}
                    </TabList>
                    {current && (
                      <VStack gap={3}>
                        <VStack gap={0.5}>
                          <Heading level={3}>{current.theme ?? `Day ${Number(day) + 1}`}</Heading>
                          <Text type="supporting">{new Date(current.date).toLocaleDateString(undefined, {weekday: 'long', month: 'long', day: 'numeric', timeZone: 'UTC'})}</Text>
                        </VStack>
                        <List hasDividers>
                          {current.slots.map(s => (
                            <ListItem
                              key={s.slot_id}
                              align="start"
                              startContent={<Text type="label" weight="semibold">{s.time}</Text>}
                              label={s.item.name}
                              description={
                                <VStack gap={2}>
                                  {s.item.why && <Text type="supporting">{s.item.why}</Text>}
                                  {!!s.item.opinion_chips?.length && (
                                    <HStack gap={1} wrap="wrap">
                                      {s.item.opinion_chips.map((c, i) => (
                                        <OpinionChip key={i} text={c.text} sourceType={c.source_type} url={c.url} />
                                      ))}
                                    </HStack>
                                  )}
                                  {!!s.item.link_ids?.length && (
                                    <HStack gap={3} wrap="wrap">
                                      {s.item.link_ids.map(id => view!.links[id]).filter(Boolean).map(l => (
                                        <Link key={l._id} href={l.url} isExternalLink>
                                          {l.label}
                                        </Link>
                                      ))}
                                    </HStack>
                                  )}
                                </VStack>
                              }
                              endContent={s.item.est_cost ? <Text type="supporting">~{s.item.est_cost.amount} {s.item.est_cost.currency}</Text> : undefined}
                            />
                          ))}
                        </List>
                      </VStack>
                    )}
                  </VStack>

                  <VStack gap={4}>
                    <Card padding={0}>
                      <TripMap slots={current?.slots ?? []} />
                    </Card>
                    <Card padding={5}>
                      <VStack gap={4}>
                        <VStack gap={2}>
                          <Heading level={3}>Booking checklist</Heading>
                          <ProgressBar
                            label="Booked"
                            value={b.checklist_progress?.booked ?? 0}
                            max={Math.max(b.checklist_progress?.total ?? b.bookings.length, 1)}
                            hasValueLabel
                            formatValueLabel={(v, m) => `${v} of ${m} booked`}
                            variant="success"
                          />
                          <Text type="supporting">Ordered by urgency. Wander links out; you book directly.</Text>
                        </VStack>
                        {grouped.map(g => (
                          <VStack key={g.title} gap={1}>
                            <Divider label={g.title} />
                            <List density="compact">
                              {g.items.map(bk => (
                                <ListItem
                                  key={bk.link_id}
                                  label={
                                    <Link href={bk.url} isExternalLink>
                                      {bk.label}
                                    </Link>
                                  }
                                  description={bk.deadline_hint ?? undefined}
                                  endContent={
                                    isOwner ? (
                                      <Switch label={`Booked: ${bk.label}`} isLabelHidden size="sm" value={bk.status === 'user_marked_booked'} onChange={v => toggle(bk, v)} />
                                    ) : bk.status === 'user_marked_booked' ? (
                                      <Badge label="Booked" variant="success" />
                                    ) : undefined
                                  }
                                />
                              ))}
                            </List>
                          </VStack>
                        ))}
                      </VStack>
                    </Card>
                  </VStack>
                </Grid>
              </VStack>
            )}
          </LayoutContent>
        }
      />
    </AppFrame>
  );
}
