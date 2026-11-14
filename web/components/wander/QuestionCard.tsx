'use client';

import {useState} from 'react';
import {Card} from '@astryxdesign/core/Card';
import {VStack, HStack} from '@astryxdesign/core/Layout';
import {Heading, Text} from '@astryxdesign/core/Text';
import {RadioList, RadioListItem} from '@astryxdesign/core/RadioList';
import {CheckboxList, CheckboxListItem} from '@astryxdesign/core/CheckboxList';
import {DateRangeInput} from '@astryxdesign/core/DateRangeInput';
import type {DateRange} from '@astryxdesign/core/DateRangeInput';
import {NumberInput} from '@astryxdesign/core/NumberInput';
import {TextInput} from '@astryxdesign/core/TextInput';
import {Button} from '@astryxdesign/core/Button';
import {Divider} from '@astryxdesign/core/Divider';
import type {QuestionItem, Questions} from '@/lib/api';

type Answer = string | string[] | number | DateRange | null;
const OTHER = '__other__';

/** "Ask like Claude/Cursor": a few sharp questions, tappable options + Other, one-line why. */
export function QuestionCard({questions, onSubmit, isSubmitting}: {questions: Questions; onSubmit: (a: Record<string, unknown>) => void; isSubmitting: boolean}) {
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [other, setOther] = useState<Record<string, string>>({});
  const set = (id: string, v: Answer) => setAnswers(a => ({...a, [id]: v}));

  const resolved = () => {
    const out: Record<string, unknown> = {};
    for (const q of questions.items) {
      const v = answers[q.id];
      if (v === OTHER) out[q.id] = other[q.id]?.trim() || null;
      else if (v !== undefined && v !== null) out[q.id] = v;
    }
    return out;
  };
  const missing = questions.items.filter(q => q.required && (resolved()[q.id] === undefined || resolved()[q.id] === null));

  return (
    <Card padding={5} width="100%">
      <VStack gap={5}>
        <VStack gap={1}>
          <Heading level={3}>{questions.batch === 1 ? 'A few quick questions' : 'Just one more thing'}</Heading>
          <Text type="supporting">Tap an option or pick Other. Each one changes the plan.</Text>
        </VStack>
        {questions.items.map((q, i) => (
          <VStack key={q.id} gap={3}>
            {i > 0 && <Divider />}
            <Field q={q} value={answers[q.id] ?? null} other={other[q.id] ?? ''} onChange={v => set(q.id, v)} onOther={t => setOther(o => ({...o, [q.id]: t}))} />
          </VStack>
        ))}
        <HStack gap={3} vAlign="center" hAlign="between" wrap="wrap">
          <Text type="supporting">{missing.length ? `${missing.length} required left` : 'All set'}</Text>
          <Button label="Continue" variant="primary" isLoading={isSubmitting} isDisabled={missing.length > 0} onClick={() => onSubmit(resolved())} />
        </HStack>
      </VStack>
    </Card>
  );
}

function Field({q, value, other, onChange, onOther}: {q: QuestionItem; value: Answer; other: string; onChange: (v: Answer) => void; onOther: (t: string) => void}) {
  const desc = q.hint ? `${q.why} · You mentioned ${q.hint}` : q.why;
  switch (q.type) {
    case 'single':
      return (
        <VStack gap={2}>
          <RadioList label={q.prompt} description={desc} value={(value as string) ?? ''} onChange={onChange} isRequired={q.required}>
            {q.options.map(o => (
              <RadioListItem key={o.id} value={o.id} label={o.label.split(' — ')[0]} description={o.label.split(' — ')[1]} />
            ))}
            <RadioListItem value={OTHER} label="Other" />
          </RadioList>
          {value === OTHER && <TextInput label="Tell us" isLabelHidden placeholder="Your answer" value={other} onChange={onOther} hasAutoFocus />}
        </VStack>
      );
    case 'multi':
      return (
        <CheckboxList label={q.prompt} description={desc} value={(value as string[]) ?? []} onChange={onChange} isOptional={!q.required}>
          {q.options.map(o => (
            <CheckboxListItem key={o.id} value={o.id} label={o.label} />
          ))}
        </CheckboxList>
      );
    case 'date_range':
      return (
        <DateRangeInput
          label={q.prompt}
          description={desc}
          value={(value as DateRange) ?? null}
          onChange={onChange}
          min={new Date().toISOString().slice(0, 10) as DateRange['start']}
          isRequired={q.required}
          width="100%"
        />
      );
    case 'number':
      return (
        <NumberInput
          label={q.prompt}
          description={desc}
          value={(value as number) ?? null}
          onChange={onChange}
          min={1}
          max={20}
          isIntegerOnly
          hasNumberSteppers
          isRequired={q.required}
          width={200}
        />
      );
    default:
      return <TextInput label={q.prompt} description={desc} value={(value as string) ?? ''} onChange={onChange} isOptional={!q.required} />;
  }
}
