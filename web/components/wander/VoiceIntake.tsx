'use client';

import {useCallback, useState} from 'react';
import {ConversationProvider, useConversation} from '@elevenlabs/react';
import {Button} from '@astryxdesign/core/Button';
import {Icon} from '@astryxdesign/core/Icon';
import {MicrophoneIcon, StopIcon} from '@heroicons/react/24/outline';
import {api, type Questions} from '@/lib/api';

/**
 * Voice intake (APP-07). The ElevenLabs agent calls two CLIENT tools that run here in the browser and hit our
 * API with the user's cookie, so ElevenLabs never needs to reach our server (no public URL).
 */
export function VoiceIntake(props: {sessionId: string; onAnswered: () => void; onError: (m: string) => void}) {
  return (
    <ConversationProvider>
      <VoiceButton {...props} />
    </ConversationProvider>
  );
}

function VoiceButton({sessionId, onAnswered, onError}: {sessionId: string; onAnswered: () => void; onError: (m: string) => void}) {
  const [isStarting, setIsStarting] = useState(false);

  const getOpenQuestions = useCallback(async () => {
    const q = await api<Questions | {items: []; status: 'none'}>(`/api/sessions/${sessionId}/questions/open`);
    if (!q.items.length) return 'No open questions. Tell the traveler their board is being prepared, then end the call.';
    return JSON.stringify(
      q.items.map(i => ({id: i.id, question: i.prompt, type: i.type, options: i.options.map(o => ({id: o.id, label: o.label})), required: i.required})),
    );
  }, [sessionId]);

  const submitAnswers = useCallback(
    async ({answers_json}: {answers_json?: string}) => {
      let answers: Record<string, unknown> = {};
      try {
        answers = JSON.parse(answers_json || '{}');
      } catch {
        return 'answers_json was not valid JSON. Send an object like {"pace":"relaxed","travelers":2}.';
      }
      try {
        await api(`/api/sessions/${sessionId}/answers`, {body: {answers}});
        onAnswered();
        const next = await getOpenQuestions();
        return next.startsWith('[') ? `Saved. Next questions: ${next}` : 'Saved. All questions answered — say goodbye and end the call.';
      } catch (e) {
        return `Could not save: ${(e as Error).message}`;
      }
    },
    [sessionId, onAnswered, getOpenQuestions],
  );

  const conversation = useConversation({
    clientTools: {
      get_open_questions: getOpenQuestions,
      submit_answers: submitAnswers as (p: Record<string, unknown>) => Promise<string>,
    },
    onError: (m: unknown) => onError(typeof m === 'string' ? m : 'Voice connection failed.'),
  });

  const isLive = conversation.status === 'connected' || conversation.status === 'connecting';

  const start = async () => {
    setIsStarting(true);
    try {
      await navigator.mediaDevices.getUserMedia({audio: true});
      const {signed_url} = await api<{signed_url: string}>('/api/voice/session', {body: {}});
      conversation.startSession({signedUrl: signed_url, dynamicVariables: {session_id: sessionId}});
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Microphone permission is needed for voice.');
    } finally {
      setIsStarting(false);
    }
  };

  return isLive ? (
    <Button label="End voice" variant="destructive" size="sm" icon={<Icon icon={StopIcon} size="sm" />} onClick={() => conversation.endSession()} />
  ) : (
    <Button label="Answer by voice" variant="ghost" size="sm" isLoading={isStarting} icon={<Icon icon={MicrophoneIcon} size="sm" />} onClick={start} />
  );
}
