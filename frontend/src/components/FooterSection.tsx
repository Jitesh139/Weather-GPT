
import { Database, Shield, BookOpen, Activity } from 'lucide-react';

export const FooterSection: React.FC = () => {
  return (
    <section className="relative w-full min-h-[40vh] flex flex-col justify-end pointer-events-none">
      {/* Backdrop overlay for glassmorphism */}
      <div className="absolute inset-0 w-full h-full backdrop-blur-[32px] brightness-75 -z-10 pointer-events-auto border-t border-white/5" />

      <div className="w-full max-w-7xl mx-auto px-5 sm:px-6 py-8 sm:py-12 pointer-events-auto">
        <div className="grid grid-cols-2 sm:grid-cols-2 md:grid-cols-4 gap-6 sm:gap-8">
          
          <div className="flex flex-col space-y-3">
            <h4 className="font-display text-lg text-textPrimary">Mausam GPT</h4>
            <p className="text-xs font-mono text-textMuted uppercase tracking-wider">
              Atmospheric Analytics Engine
            </p>
          </div>

          <div className="flex flex-col space-y-3">
            <h4 className="font-sans text-sm text-textPrimary uppercase tracking-wider flex items-center space-x-2">
              <Database className="w-4 h-4 text-accent" />
              <span>Data Source</span>
            </h4>
            <ul className="text-sm font-sans text-textMuted space-y-2">
              <li>Open-Meteo Integration</li>
              <li>NOAA Global Forecasting</li>
              <li>Real-time Ingestion</li>
            </ul>
          </div>

          <div className="flex flex-col space-y-3">
            <h4 className="font-sans text-sm text-textPrimary uppercase tracking-wider flex items-center space-x-2">
              <BookOpen className="w-4 h-4 text-accent" />
              <span>Resources</span>
            </h4>
            <ul className="text-sm font-sans text-textMuted space-y-2">
              <li>Model Documentation</li>
              <li>Parameter Definitions</li>
              <li>API Interface</li>
            </ul>
          </div>

          <div className="flex flex-col space-y-3">
            <h4 className="font-sans text-sm text-textPrimary uppercase tracking-wider flex items-center space-x-2">
              <Shield className="w-4 h-4 text-accent" />
              <span>Trust & Status</span>
            </h4>
            <ul className="text-sm font-sans text-textMuted space-y-2">
              <li className="flex items-center space-x-2">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                </span>
                <span>All Systems Operational</span>
              </li>
              <li>Privacy Policy</li>
              <li>Terms of Service</li>
            </ul>
          </div>

        </div>
        
        <div className="mt-8 sm:mt-12 pt-6 border-t border-white/10 flex flex-wrap gap-2 justify-between items-center text-xs font-mono text-textMuted">
          <p>© {new Date().getFullYear()} Mausam GPT. All rights reserved.</p>
          <div className="flex items-center space-x-1">
            <Activity className="w-3 h-3" />
            <span>v1.0.0</span>
          </div>
        </div>
      </div>
    </section>
  );
};
