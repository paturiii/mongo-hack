'use client';

import {Suspense, useState} from 'react';
import {useRouter, useSearchParams} from 'next/navigation';
import {VStack} from '@astryxdesign/core/Layout';
import {Center} from '@astryxdesign/core/Center';
import {Text, Heading} from '@astryxdesign/core/Text';
import {TextInput} from '@astryxdesign/core/TextInput';
import {Button} from '@astryxdesign/core/Button';
import {Card} from '@astryxdesign/core/Card';
import {Icon} from '@astryxdesign/core/Icon';
import {Banner} from '@astryxdesign/core/Banner';
import {SegmentedControl, SegmentedControlItem} from '@astryxdesign/core/SegmentedControl';
import {GlobeEuropeAfricaIcon} from '@heroicons/react/24/outline';
import {api} from '@/lib/api';

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get('next') || '/';
  const [mode, setMode] = useState<'login' | 'signup'>('login');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    setError('');
    if (!email || !password) return setError('Enter your email and password.');
    setIsLoading(true);
    try {
      await api(mode === 'login' ? '/auth/login' : '/auth/signup', {body: mode === 'login' ? {email, password} : {email, password, name}});
      router.replace(next.startsWith('/') ? next : '/');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Center axis="both" padding={6} height="100dvh">
      <VStack gap={4} hAlign="center" width="100%" maxWidth={400}>
        <VStack gap={2} hAlign="center">
          <Icon icon={GlobeEuropeAfricaIcon} size="lg" color="accent" />
          <Text type="body" weight="bold" size="lg">
            Wander
          </Text>
        </VStack>
        <Card padding={8} width="100%">
          <VStack gap={4} hAlign="stretch">
            <VStack gap={1} hAlign="center">
              <Heading level={2}>{mode === 'login' ? 'Sign in' : 'Create your account'}</Heading>
              <Text type="body" color="secondary" size="sm">
                {mode === 'login' ? 'Pick up your trips where you left off' : 'Wander remembers how you like to travel'}
              </Text>
            </VStack>
            <SegmentedControl label="Account" value={mode} onChange={v => setMode(v as 'login' | 'signup')} layout="fill">
              <SegmentedControlItem value="login" label="Sign in" />
              <SegmentedControlItem value="signup" label="Create account" />
            </SegmentedControl>
            {error && <Banner status="error" title={error} container="card" />}
            {mode === 'signup' && <TextInput label="Name" value={name} onChange={setName} placeholder="Ana" size="lg" />}
            <TextInput label="Email" value={email} onChange={setEmail} placeholder="you@example.com" type="email" size="lg" />
            <TextInput
              label="Password"
              value={password}
              onChange={setPassword}
              placeholder={mode === 'signup' ? 'At least 8 characters' : 'Your password'}
              type="password"
              size="lg"
              onEnter={submit}
            />
            <Button label={mode === 'login' ? 'Sign in' : 'Create account'} variant="primary" size="lg" isLoading={isLoading} onClick={submit} />
          </VStack>
        </Card>
      </VStack>
    </Center>
  );
}
