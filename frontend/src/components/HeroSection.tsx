

export const HeroSection: React.FC = () => {
  return (
    <section className="relative w-full h-screen flex flex-col items-center justify-start pt-[25vh]">
      <h1 className="font-display text-6xl md:text-8xl text-textPrimary text-shadow-hero mb-8 tracking-[-0.02em] font-medium z-10 text-center">
        Mausam GPT
      </h1>
      
      {/* Ticker */}
      <div className="absolute bottom-20 w-full overflow-hidden border-y border-white/10 bg-black/20 backdrop-blur-md py-3 flex">
        <div className="whitespace-nowrap animate-marquee flex space-x-8 font-mono text-sm tracking-widest text-textPrimary uppercase">
          <span>Verifiable Weather Intelligence</span>
          <span>•</span>
          <span>Zero Ungrounded Hallucinations</span>
          <span>•</span>
          <span>Real-Time Open-Meteo Ingestion</span>
          <span>•</span>
          <span>Voice & Text Interface</span>
          <span>•</span>
          {/* Duplicate for seamless looping */}
          <span>Verifiable Weather Intelligence</span>
          <span>•</span>
          <span>Zero Ungrounded Hallucinations</span>
          <span>•</span>
          <span>Real-Time Open-Meteo Ingestion</span>
          <span>•</span>
          <span>Voice & Text Interface</span>
        </div>
      </div>
    </section>
  );
};
