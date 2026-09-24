'use client';

import * as React from 'react';
import {
  NavigationMenu,
  NavigationMenuContent,
  NavigationMenuItem,
  NavigationMenuLink,
  NavigationMenuList,
  NavigationMenuTrigger,
  navigationMenuTriggerStyle,
} from '@/components/ui/navigation-menu';
import { CloudRainIcon, ActivityIcon, ServerIcon } from 'lucide-react';

const Link = React.forwardRef<HTMLAnchorElement, React.ComponentPropsWithoutRef<'a'>>(({ href, ...props }, ref) => (
  <a href={href} ref={ref} {...props} />
));
Link.displayName = 'Link';

const features: { title: string; href: string; description: string }[] = [
  {
    title: 'Voice Interface',
    href: '#voice-input',
    description: 'Native Web Speech API integration for hands-free meteorological queries.',
  },
  {
    title: 'Fast Path Routing',
    href: '#fast-path',
    description: 'Direct factual lookups bypassing full LLM generation for instant data retrieval.',
  },
  {
    title: 'LLM Verification',
    href: '#slow-path',
    description: 'Stage 2 verifier checks every drafted numerical claim against raw Open-Meteo data.',
  },
  {
    title: 'Data Grounding',
    href: '#grounding',
    description: 'Strict fail-loud constraints ensuring zero unverified numbers are presented.',
  },
  {
    title: 'Persona Optics',
    href: '#personas',
    description: 'Customized data extraction tailored for Farmers, Citizens, or Researchers.',
  },
  {
    title: 'Persistent Cache',
    href: '#architecture',
    description: 'PostgreSQL-backed TTL caching to minimize redundant location telemetry calls.',
  },
];

function ListItem({ title, children, href, ...props }: React.ComponentPropsWithoutRef<'li'> & { href: string }) {
  return (
    <li {...props}>
      <NavigationMenuLink asChild>
        <Link href={href}>
          <div className="text-sm leading-none font-medium">{title}</div>
          <p className="text-muted-foreground line-clamp-2 text-sm leading-snug">{children}</p>
        </Link>
      </NavigationMenuLink>
    </li>
  );
}

export default function Component() {
  return (
    <>
      {/* Mobile: a single compact brand pill - the full mega-menu doesn't
          fit a phone screen and this is a scroll-driven one-pager anyway,
          so the primary nav isn't needed to get around. */}
      <div className="flex md:hidden items-center gap-2 rounded-full border border-white/10 bg-white/5 backdrop-blur-md px-4 py-2">
        <span className="font-display text-sm text-sky-400">Mausam GPT</span>
      </div>

      <div className="hidden md:block">
        <NavigationMenu viewport={false}>
      <NavigationMenuList>
        <NavigationMenuItem>
          <NavigationMenuTrigger>Platform</NavigationMenuTrigger>
          <NavigationMenuContent>
            <ul className="grid gap-2 md:w-[400px] lg:w-[500px] lg:grid-cols-[.75fr_1fr]">
              <li className="row-span-3">
                <NavigationMenuLink asChild>
                  <Link
                    className="from-slate-900/50 to-slate-900 flex h-full w-full flex-col justify-center rounded-md bg-linear-to-b p-6 no-underline outline-hidden select-none focus:shadow-md border border-white/10"
                    href="/"
                  >
                    <div className="-mt-6 mb-2 text-xl font-bold text-sky-400">Mausam GPT</div>
                    <p className="text-slate-300 text-sm leading-tight">
                      Verifiable weather intelligence powered by Open-Meteo and strictly grounded LLM architecture.
                    </p>
                  </Link>
                </NavigationMenuLink>
              </li>
              <ListItem href="#architecture" title="Architecture">
                Understand the Fast vs. Slow path routing mechanisms.
              </ListItem>
              <ListItem href="#data-sources" title="Data Sources">
                Explore our Open-Meteo integration and geographic constraints.
              </ListItem>
              <ListItem href="#fail-loud" title="Error Handling">
                How the system prevents hallucinations through strict fallback UI.
              </ListItem>
            </ul>
          </NavigationMenuContent>
        </NavigationMenuItem>
        <NavigationMenuItem>
          <NavigationMenuTrigger>Features</NavigationMenuTrigger>
          <NavigationMenuContent>
            <ul className="grid w-[400px] gap-2 md:w-[500px] md:grid-cols-2 lg:w-[600px]">
              {features.map((feature) => (
                <ListItem key={feature.title} title={feature.title} href={feature.href}>
                  {feature.description}
                </ListItem>
              ))}
            </ul>
          </NavigationMenuContent>
        </NavigationMenuItem>
        <NavigationMenuItem>
          <NavigationMenuLink asChild className={navigationMenuTriggerStyle()}>
            <Link href="#personas">Personas</Link>
          </NavigationMenuLink>
        </NavigationMenuItem>
        <NavigationMenuItem>
          <NavigationMenuTrigger>Sources</NavigationMenuTrigger>
          <NavigationMenuContent>
            <ul className="grid w-[300px] gap-4">
              <li>
                <NavigationMenuLink asChild>
                  <Link href="#">
                    <div className="font-medium">Open-Meteo API</div>
                    <div className="text-muted-foreground">Primary source for global NWP models.</div>
                  </Link>
                </NavigationMenuLink>
                <NavigationMenuLink asChild>
                  <Link href="#">
                    <div className="font-medium">Web Speech API</div>
                    <div className="text-muted-foreground">Browser-native voice transcription.</div>
                  </Link>
                </NavigationMenuLink>
                <NavigationMenuLink asChild>
                  <Link href="#">
                    <div className="font-medium">Future Integrations</div>
                    <div className="text-muted-foreground">Roadmap for NDMA and MOSDAC data.</div>
                  </Link>
                </NavigationMenuLink>
              </li>
            </ul>
          </NavigationMenuContent>
        </NavigationMenuItem>
        <NavigationMenuItem>
          <NavigationMenuTrigger>Legal</NavigationMenuTrigger>
          <NavigationMenuContent>
            <ul className="grid w-[200px] gap-4">
              <li>
                <NavigationMenuLink asChild>
                  <Link href="#">Privacy Policy</Link>
                </NavigationMenuLink>
                <NavigationMenuLink asChild>
                  <Link href="#">API Disclaimers</Link>
                </NavigationMenuLink>
                <NavigationMenuLink asChild>
                  <Link href="#">Data Attribution</Link>
                </NavigationMenuLink>
              </li>
            </ul>
          </NavigationMenuContent>
        </NavigationMenuItem>
        <NavigationMenuItem>
          <NavigationMenuTrigger>Status</NavigationMenuTrigger>
          <NavigationMenuContent>
            <ul className="grid w-[200px] gap-4">
              <li>
                <NavigationMenuLink asChild>
                  <Link href="#" className="flex-row items-center gap-2">
                    <ActivityIcon className="size-4 text-emerald-400" />
                    Systems Operational
                  </Link>
                </NavigationMenuLink>
                <NavigationMenuLink asChild>
                  <Link href="#" className="flex-row items-center gap-2">
                    <CloudRainIcon className="size-4 text-sky-400" />
                    Data Freshness
                  </Link>
                </NavigationMenuLink>
                <NavigationMenuLink asChild>
                  <Link href="#" className="flex-row items-center gap-2">
                    <ServerIcon className="size-4 text-slate-400" />
                    Cache Ping
                  </Link>
                </NavigationMenuLink>
              </li>
            </ul>
          </NavigationMenuContent>
        </NavigationMenuItem>
      </NavigationMenuList>
        </NavigationMenu>
      </div>
    </>
  );
}
