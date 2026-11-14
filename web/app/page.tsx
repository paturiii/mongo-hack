'use client';

import {useEffect, useMemo, useState} from 'react';
import {useRouter} from 'next/navigation';
import {Layout, LayoutContent, VStack, HStack} from '@astryxdesign/core/Layout';
import {Text, Heading} from '@astryxdesign/core/Text';
import {ChatComposer} from '@astryxdesign/core/Chat';
import {Typeahead, type SearchableItem} from '@astryxdesign/core/Typeahead';
import {ClickableCard} from '@astryxdesign/core/ClickableCard';
import {Grid} from '@astryxdesign/core/Grid';
import {Icon} from '@astryxdesign/core/Icon';
import {List, ListItem} from '@astryxdesign/core/List';
import {Badge} from '@astryxdesign/core/Badge';
import {Banner} from '@astryxdesign/core/Banner';
import {Skeleton} from '@astryxdesign/core/Skeleton';
import {MapPinIcon, SparklesIcon} from '@heroicons/react/24/outline';
import {AppFrame} from '@/components/wander/AppFrame';
import {api, type SessionSummary, type SessionView} from '@/lib/api';
import {useMe} from '@/lib/useMe';

const SUGGESTIONS = [
  {heading: 'Food & nightlife', body: '5 days in Lisbon in March, food and nightlife, flying from London'},
  {heading: 'Slow and local', body: 'A relaxed long weekend in Porto, mostly food and wine, no early starts'},
  {heading: 'First time abroad', body: 'One week in Tokyo for two, first time in Japan, love art and street food'},
];

const STATUS: Record<string, {label: string; variant: 'info' | 'success' | 'warning' | 'neutral'}> = {
  asking: {label: 'Needs answers', variant: 'warning'},
  researching: {label: 'Researching', variant: 'info'},
  planning: {label: 'Planning', variant: 'info'},
  refining: {label: 'Updating', variant: 'info'},
  ready: {label: 'Board ready', variant: 'success'},
};

type City = SearchableItem<{country: string}>;

export default function HomePage() {
  const router = useRouter();
  const {me} = useMe();
  const [prompt, setPrompt] = useState('');
  const [city, setCity] = useState<City | null>(null);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState('');
  const [trips, setTrips] = useState<SessionSummary[] | null>(null);

  useEffect(() => {
    if (me) api<SessionSummary[]>('/api/sessions').then(setTrips).catch(() => setTrips([]));
  }, [me]);

  const cities = useMemo(
    () => ({
      search: async (q: string): Promise<City[]> =>
        (await api<{name: string; country: string}[]>(`/api/autocomplete?q=${encodeURIComponent(q)}`)).map(c => ({
          id: `${c.name}|${c.country}`,
          label: c.name,
          auxiliaryData: {country: c.country},
        })),
      bootstrap: () => [],
    }),
    [],
  );

  const start = async (text: string) => {
    let p = text.trim();
    if (city && !p.toLowerCase().includes(city.label.toLowerCase())) p = `${p}${p ? ' — ' : ''}trip to ${city.label}`;
    if (!p) return;
    setError('');
    setIsSending(true);
    try {
      const v = await api<SessionView>('/api/trips', {body: {prompt: p}});
      router.push(`/trip/${v.session.id}`);
    } catch (e) {
      setError((e as Error).message);
      setIsSending(false);
    }
  };

  return (
    <AppFrame me={me}>
      <Layout
        height="fill"
        contentWidth={720}
        padding={6}
        content={
          <LayoutContent>
            <VStack gap={8} vAlign="center" minHeight="100%">
              <VStack gap={1}>
                <HStack gap={2} vAlign="center">
                  <Icon icon={SparklesIcon} size="md" color="accent" />
                  <Text type="large" as="p">
                    {me ? `Hi, ${me.name}` : 'Hi'}
                  </Text>
                </HStack>
                <Text type="display-2" as="p">
                  Where to next?
                </Text>
                <Text type="supporting">I ask a few sharp questions, read what travelers really say, and hand you one board of booking links.</Text>
              </VStack>

              {error && <Banner status="error" title={error} isDismissable onDismiss={() => setError('')} />}

              <VStack gap={3}>
                <Typeahead<City>
                  label="Destination"
                  description="Optional: pick a city, then describe the trip"
                  placeholder="Lisbon, Tokyo, Mexico City…"
                  searchSource={cities}
                  value={city}
                  onChange={setCity}
                  startIcon={MapPinIcon}
                  minQueryLength={2}
                />
                <ChatComposer
                  value={prompt}
                  onChange={setPrompt}
                  onSubmit={start}
                  isDisabled={isSending}
                  placeholder="5 days in Lisbon in March, food and nightlife, flying TP1351 from London"
                  headerContext={isSending ? <Text type="supporting">Reading your request…</Text> : undefined}
                />
              </VStack>

              <Grid columns={{minWidth: 200}} gap={3}>
                {SUGGESTIONS.map(s => (
                  <ClickableCard key={s.heading} label={s.heading} variant="muted" padding={3} isDisabled={isSending} onClick={() => start(s.body)}>
                    <VStack gap={0.5}>
                      <Heading level={4}>{s.heading}</Heading>
                      <Text type="body" color="secondary" size="xsm">
                        {s.body}
                      </Text>
                    </VStack>
                  </ClickableCard>
                ))}
              </Grid>

              <VStack gap={2}>
                <Heading level={3}>Your trips</Heading>
                {trips === null ? (
                  <Skeleton height={56} />
                ) : trips.length === 0 ? (
                  <Text type="supporting">No trips yet. Describe one above.</Text>
                ) : (
                  <List hasDividers>
                    {trips.map(t => {
                      const st = STATUS[t.status] ?? {label: t.status, variant: 'neutral' as const};
                      return (
                        <ListItem
                          key={t.id}
                          href={`/trip/${t.id}`}
                          label={t.board?.title || t.destination || 'New trip'}
                          description={t.first_message}
                          endContent={<Badge label={st.label} variant={st.variant} />}
                        />
                      );
                    })}
                  </List>
                )}
              </VStack>
            </VStack>
          </LayoutContent>
        }
      />
    </AppFrame>
  );
}
