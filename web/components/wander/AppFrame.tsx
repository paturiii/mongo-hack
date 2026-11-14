'use client';

import {useRouter} from 'next/navigation';
import {AppShell} from '@astryxdesign/core/AppShell';
import {TopNav, TopNavHeading, TopNavItem} from '@astryxdesign/core/TopNav';
import {DropdownMenu} from '@astryxdesign/core/DropdownMenu';
import {Icon} from '@astryxdesign/core/Icon';
import {Button} from '@astryxdesign/core/Button';
import {GlobeEuropeAfricaIcon, UserCircleIcon} from '@heroicons/react/24/outline';
import {api, type Me} from '@/lib/api';

/** App chrome: top nav with trips + memory, account menu. */
export function AppFrame({me, children, contentPadding = 0}: {me: Me | null; children: React.ReactNode; contentPadding?: 0 | 4}) {
  const router = useRouter();
  const signOut = async () => {
    await api('/auth/logout', {body: {}});
    router.replace('/login');
  };

  return (
    <AppShell
      height="fill"
      variant="section"
      contentPadding={contentPadding}
      topNav={
        <TopNav
          heading={<TopNavHeading logo={<Icon icon={GlobeEuropeAfricaIcon} size="md" color="accent" />} heading="Wander" headingHref="/" />}
          startContent={
            me ? (
              <>
                <TopNavItem href="/" label="Plan a trip" />
                <TopNavItem href="/memory" label="What Wander remembers" />
              </>
            ) : null
          }
          endContent={
            me ? (
              <DropdownMenu
                button={{label: me.name || me.email, variant: 'ghost', size: 'sm', icon: <Icon icon={UserCircleIcon} size="sm" />, children: me.name || me.email}}
                menuWidth={220}
                items={[
                  {label: `${me.usage_today}/${me.quota} AI calls today`, onClick: () => {}},
                  {type: 'divider' as const},
                  {label: 'Sign out', onClick: signOut},
                ]}
              />
            ) : (
              <Button label="Sign in" variant="primary" size="sm" href="/login" />
            )
          }
        />
      }>
      {children}
    </AppShell>
  );
}
