'use client';

import {use, useCallback, useEffect, useState} from 'react';
import {VStack, HStack} from '@astryxdesign/core/Layout';
import {Text, Heading} from '@astryxdesign/core/Text';
import {ChatLayout, ChatMessageList, ChatMessage, ChatMessageBubble, ChatComposer, ChatSystemMessage} from '@astryxdesign/core/Chat';
import {Avatar} from '@astryxdesign/core/Avatar';
import {Card} from '@astryxdesign/core/Card';
import {ProgressBar} from '@astryxdesign/core/ProgressBar';
import {Button} from '@astryxdesign/core/Button';
import {Banner} from '@astryxdesign/core/Banner';
import {Badge} from '@astryxdesign/core/Badge';
import {Spinner} from '@astryxdesign/core/Spinner';
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {AppFrame} from '@/components/wander/AppFrame';
import {QuestionCard} from '@/components/wander/QuestionCard';
import {VoiceIntake} from '@/components/wander/VoiceIntake';
import {api, type Progress, type SessionView} from '@/lib/api';
import {useMe} from '@/lib/useMe';

const KIND_LABEL: Record<string, string> = {
  opinions: 'Reading traveler opinions',
  places: 'Finding places',
  stay: 'Finding stays',
  getting_there: 'Getting there',
  transit: 'Getting around',
  tickets: 'Finding tickets',
  nightlife: 'Checking nights out on your dates',
  restaurants: 'Finding tables',
  link_resolve: 'Checking links',
};

export default function TripPage({params}: {params: Promise<{sid: string}>}) {
  const {sid} = use(params);
  const {me} = useMe();
  const [view, setView] = useState<SessionView | null>(null);
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const load = useCallback(() => api<SessionView>(`/api/sessions/${sid}`).then(setView).catch(e => setError(e.message)), [sid]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while the backend is working (research progress, board landing, refine).
  const status = view?.session.status;
  useEffect(() => {
    if (!status || !['researching', 'planning', 'refining'].includes(status)) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [status, load]);

  const answer = async (answers: Record<string, unknown>) => {
    setIsSubmitting(true);
    setError('');
    try {
      setView(await api<SessionView>(`/api/sessions/${sid}/answers`, {body: {answers}}));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const refine = async (instruction: string) => {
    if (!instruction.trim()) return;
    try {
      setView(await api<SessionView>(`/api/sessions/${sid}/refine`, {body: {instruction}}));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const ready = !!view?.board;
  const asking = !!view?.questions;

  return (
    <AppFrame me={me}>
      <VStack height="100%">
      <ChatLayout
        density="spacious"
        composer={
          <ChatComposer
            onSubmit={refine}
            isDisabled={!ready}
            placeholder={ready ? 'Change anything: “make day 3 a beach day”, “cheaper stay”…' : asking ? 'Answer the questions above, or use voice' : 'Working on your plan…'}
            sendActions={asking && view ? <VoiceIntake sessionId={sid} onAnswered={load} onError={setError} /> : undefined}
            status={error ? {type: 'error', message: error} : undefined}
          />
        }
        emptyState={
          <HStack gap={2} hAlign="center" vAlign="center">
            <Spinner size="md" />
            <Text type="supporting">Loading your trip…</Text>
          </HStack>
        }>
        {view ? (
          <ChatMessageList>
            <TripHeader view={view} />
            {view.session.messages.map((m, i) => (
              <ChatMessage key={i} sender={m.role === 'user' ? 'user' : 'assistant'} avatar={m.role === 'user' ? undefined : <Avatar name="Wander" size="md" />}>
                <ChatMessageBubble variant={m.role === 'user' ? 'filled' : 'ghost'}>{m.content}</ChatMessageBubble>
              </ChatMessage>
            ))}
            {view.questions && (
              <ChatMessage sender="assistant" avatar={<Avatar name="Wander" size="md" />}>
                <ChatMessageBubble variant="ghost" width="100%">
                  <QuestionCard key={view.questions._id} questions={view.questions} onSubmit={answer} isSubmitting={isSubmitting} />
                </ChatMessageBubble>
              </ChatMessage>
            )}
            {!view.questions && !ready && view.progress && (
              <ChatMessage sender="assistant" avatar={<Avatar name="Wander" size="md" />}>
                <ChatMessageBubble variant="ghost" width="100%">
                  <ResearchProgress p={view.progress} city={view.trip.destination?.name} />
                </ChatMessageBubble>
              </ChatMessage>
            )}
            {view.board && (
              <ChatMessage sender="assistant" avatar={<Avatar name="Wander" size="md" />}>
                <ChatMessageBubble variant="ghost" width="100%">
                  <Card padding={5} variant="muted" width="100%">
                    <VStack gap={3}>
                      <HStack gap={2} vAlign="center" wrap="wrap">
                        <Heading level={3}>Your board is ready</Heading>
                        <Badge label={`v${view.board.version}`} variant="success" />
                      </HStack>
                      <Text type="body">{view.board.title}</Text>
                      <HStack gap={2} wrap="wrap">
                        <Button label="Open board" variant="primary" href={`/b/${view.board.slug}`} />
                        <Button label="Copy share link" onClick={() => navigator.clipboard.writeText(`${window.location.origin}/b/${view.board!.slug}`)} />
                      </HStack>
                    </VStack>
                  </Card>
                </ChatMessageBubble>
              </ChatMessage>
            )}
            {view.session.status === 'refining' && <ChatSystemMessage>Updating your board…</ChatSystemMessage>}
          </ChatMessageList>
        ) : error ? (
          <Banner status="error" title={error} />
        ) : null}
      </ChatLayout>
      </VStack>
    </AppFrame>
  );
}

function TripHeader({view}: {view: SessionView}) {
  const t = view.trip;
  if (!t.destination) return null;
  const dates = t.dates ? `${t.dates.start.slice(0, 10)} → ${t.dates.end.slice(0, 10)}` : 'Dates to confirm';
  return (
    <ChatSystemMessage variant="divider">
      {`${t.destination.name}${t.destination.country ? `, ${t.destination.country}` : ''} · ${dates}${t.travelers ? ` · ${t.travelers} traveling` : ''}`}
    </ChatSystemMessage>
  );
}

function ResearchProgress({p, city}: {p: Progress; city?: string}) {
  const kinds = Object.entries(p.by_kind);
  // All research jobs done but no board yet = planner is ranking places and laying out days (phase 2).
  const building = p.jobs_total > 0 && p.jobs_done >= p.jobs_total;
  return (
    <Card padding={5} width="100%">
      <VStack gap={4}>
        <VStack gap={1}>
          <Heading level={3}>{building ? `Building your ${city ?? ''} plan` : `Researching ${city ?? 'your trip'}`}</Heading>
          <Text type="supporting">
            {building
              ? `Research done (${p.opinions} traveler opinions). Now ranking places and laying out your days — usually a few minutes.`
              : p.opinions > 0
                ? `${p.opinions} traveler opinions so far${p.sources.length ? ` from ${p.sources.join(', ')}` : ''}`
                : 'Sending scouts…'}
          </Text>
        </VStack>
        <ProgressBar
          label={building ? 'Planning' : 'Research progress'}
          value={p.jobs_done}
          max={Math.max(p.jobs_total, 1)}
          hasValueLabel={!building}
          isIndeterminate={p.jobs_total === 0 || building}
        />
        {kinds.length > 0 && (
          <MetadataList columns="multi">
            {kinds.map(([k, v]) => (
              <MetadataListItem key={k} label={KIND_LABEL[k] ?? k}>
                {v.done}/{v.total}
              </MetadataListItem>
            ))}
          </MetadataList>
        )}
      </VStack>
    </Card>
  );
}
