import { forwardRef } from 'react';
import type { Persona } from '../App';

interface PersonaSectionProps {
  onSelect: (p: Persona) => void;
  activePersona: Persona;
}

export const PersonaSection = forwardRef<HTMLElement, PersonaSectionProps>(
  ({ onSelect, activePersona }, ref) => {
    
    const personas: { id: Persona; title: string; sub: string }[] = [
      {
        id: 'farmer',
        title: 'Farmer',
        sub: 'Frost windows, precipitation schedules, high-risk threshold alerts'
      },
      {
        id: 'citizen',
        title: 'Normal Citizen',
        sub: 'Commute clarity, weekend outlooks, real-time comfort indices'
      },
      {
        id: 'researcher',
        title: 'Researcher',
        sub: 'Raw model parameter diffs, boundary metrics, synoptic observation'
      }
    ];

    return (
      <section ref={ref} className="w-full min-h-[100dvh] flex flex-col items-center justify-center py-16 sm:py-32 px-4 z-10">
        <h2 className="font-display text-3xl sm:text-4xl md:text-6xl text-textPrimary mb-8 sm:mb-16 tracking-tight text-center">
          Personalize Your Forecast
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 sm:gap-6 max-w-6xl w-full">
          {personas.map(p => {
            const isActive = activePersona === p.id;
            return (
              <div
                key={p.id}
                onClick={() => onSelect(p.id)}
                className={`
                  p-5 sm:p-8 rounded-2xl cursor-pointer border backdrop-blur-md transition-all duration-300 active:scale-[0.99]
                  ${isActive
                    ? 'border-accent bg-white/10 scale-[1.02] shadow-[0_0_30px_rgba(56,189,248,0.2)]'
                    : 'border-white/10 bg-surface hover:bg-white/[0.06] hover:scale-[1.02] hover:border-sky-400/40'
                  }
                `}
              >
                <h3 className="font-display text-xl sm:text-2xl mb-2 sm:mb-4 text-textPrimary">{p.title}</h3>
                <p className="font-sans text-sm text-textMuted leading-relaxed">
                  {p.sub}
                </p>
              </div>
            );
          })}
        </div>
      </section>
    );
  }
);

PersonaSection.displayName = 'PersonaSection';
