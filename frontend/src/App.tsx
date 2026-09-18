import { useEffect, useState, useRef } from 'react';
import Lenis from 'lenis';
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { CanvasController } from './components/CanvasController';
import { HeroSection } from './components/HeroSection';
import { PersonaSection } from './components/PersonaSection';
import { ChatSection } from './components/ChatSection';
import { FooterSection } from './components/FooterSection';
import Navigation from './components/navigation';
import { ResearcherDashboard } from './components/dashboard/ResearcherDashboard';

gsap.registerPlugin(ScrollTrigger);

export type Persona = 'farmer' | 'citizen' | 'researcher' | null;

export const RESEARCHER_ROUTE = '/dashboard/researcher';

function App() {
  const [frameIndex, setFrameIndex] = useState(0);
  const [activePersona, setActivePersona] = useState<Persona>(null);
  // Minimal client-side routing. The researcher dashboard is a distinct
  // route rather than another section of the scroll page, but the app
  // only has the one extra route, so this costs less than a router
  // dependency. The backend serves index.html for /dashboard/* so a hard
  // refresh or a shared link still lands here.
  const [route, setRoute] = useState(() => window.location.pathname);

  useEffect(() => {
    const onPop = () => setRoute(window.location.pathname);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = (path: string) => {
    window.history.pushState({}, '', path);
    setRoute(path);
    window.scrollTo({ top: 0 });
  };
  
  const headerRef = useRef<HTMLElement>(null);
  const section2Ref = useRef<HTMLElement>(null);
  const section3Ref = useRef<HTMLElement>(null);

  // Lenis Smooth Scroll Setup
  useEffect(() => {
    const lenis = new Lenis({
      duration: 1.2,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      orientation: 'vertical',
      gestureOrientation: 'vertical',
      smoothWheel: true,
      touchMultiplier: 2,
    });

    function raf(time: number) {
      lenis.raf(time);
      requestAnimationFrame(raf);
    }
    requestAnimationFrame(raf);

    // Sync Lenis with GSAP ScrollTrigger
    lenis.on('scroll', ScrollTrigger.update);
    gsap.ticker.add((time) => {
      lenis.raf(time * 1000);
    });
    gsap.ticker.lagSmoothing(0, 0);

    return () => {
      lenis.destroy();
      gsap.ticker.remove(lenis.raf);
    };
  }, []);

  // Header Fade In
  useEffect(() => {
    if (headerRef.current) {
      gsap.fromTo(
        headerRef.current,
        { opacity: 0 },
        { opacity: 1, duration: 1.5, ease: 'power2.out' }
      );
    }
  }, []);

  // Frame Animation Logic
  useEffect(() => {
    let autoplayObj = { frame: 0 };
    
    // Phase 1: Autoplay frame 0 to 114 at 30fps
    const autoplayTween = gsap.to(autoplayObj, {
      frame: 114,
      duration: 114 / 30, // 30fps
      ease: "none",
      onUpdate: () => setFrameIndex(autoplayObj.frame),
      onComplete: () => {
        // Setup ScrollTriggers only after autoplay completes
        setupScrollTriggers();
      }
    });

    const setupScrollTriggers = () => {
      if (!section2Ref.current || !section3Ref.current) return;

      // Phase 2: Section 2 scrubs frames 115 to 224
      ScrollTrigger.create({
        trigger: section2Ref.current,
        start: "top bottom", // Starts when section 2 enters viewport
        end: "bottom bottom", // Ends when section 2 is fully in view
        scrub: 1, // Smooth scrubbing
        onUpdate: (self) => {
          // progress is 0 to 1
          const frame = 115 + self.progress * (224 - 115);
          setFrameIndex(frame);
        }
      });

      // Phase 3: Section 3 scrubs frames 225 to 299
      ScrollTrigger.create({
        trigger: section3Ref.current,
        start: "top bottom",
        end: "bottom bottom",
        scrub: 1,
        onUpdate: (self) => {
          const frame = 225 + self.progress * (299 - 225);
          // Only update if we are past phase 2 to prevent conflict
          if (self.isActive) {
             setFrameIndex(frame);
          }
        }
      });
    };

    return () => {
      autoplayTween.kill();
      ScrollTrigger.getAll().forEach(t => t.kill());
    };
  }, []);

  const handlePersonaSelect = (persona: Persona) => {
    setActivePersona(persona);
    // Researchers get the dashboard; everyone else goes straight to the
    // conversational interface as before.
    if (persona === 'researcher') {
      navigate(RESEARCHER_ROUTE);
      return;
    }
    if (section3Ref.current) {
      // Smooth scroll to section 3
      window.scrollTo({
        top: section3Ref.current.offsetTop,
        behavior: 'smooth'
      });
    }
  };

  if (route.startsWith(RESEARCHER_ROUTE)) {
    return (
      <div className="relative w-full text-textPrimary">
        <CanvasController frameIndex={frameIndex} />
        <ResearcherDashboard
          onHome={() => {
            setActivePersona(null);
            navigate('/');
          }}
          onBackToChat={() => {
            // Keep the researcher persona - they're switching to the
            // conversational interface, not changing who they are.
            navigate('/');
            requestAnimationFrame(() => {
              section3Ref.current?.scrollIntoView({ behavior: 'smooth' });
            });
          }}
        />
      </div>
    );
  }

  return (
    <div className="relative w-full text-textPrimary">
      <CanvasController frameIndex={frameIndex} />
      
      <main className="relative z-10">
        <header ref={headerRef} className="fixed top-0 z-50 w-full flex justify-center p-4 opacity-0">
          <Navigation />
        </header>
        <HeroSection />
        <PersonaSection ref={section2Ref} onSelect={handlePersonaSelect} activePersona={activePersona} />
        <ChatSection 
          ref={section3Ref} 
          activePersona={activePersona} 
          onBack={() => {
            setActivePersona(null);
            if (section2Ref.current) {
              window.scrollTo({
                top: section2Ref.current.offsetTop,
                behavior: 'smooth'
              });
            }
          }} 
        />
        <FooterSection />
      </main>
    </div>
  );
}

export default App;
