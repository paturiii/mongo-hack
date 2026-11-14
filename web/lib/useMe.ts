'use client';

import {useEffect, useState} from 'react';
import {useRouter} from 'next/navigation';
import {api, ApiError, type Me} from './api';

/** Current user; redirects to /login when signed out (unless optional). */
export function useMe(opts: {optional?: boolean} = {}) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    api<Me>('/api/me')
      .then(setMe)
      .catch(e => {
        if (e instanceof ApiError && e.status === 401 && !opts.optional) {
          router.replace(`/login?next=${encodeURIComponent(window.location.pathname)}`);
        }
      })
      .finally(() => setIsLoading(false));
  }, [router, opts.optional]);

  return {me, isLoading};
}
