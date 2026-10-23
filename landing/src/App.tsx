import { useState } from 'react';
import {
  Cpu,
  ShieldCheck,
  Zap,
  Cloud,
  ChevronDown,
  ChevronUp,
  CodeXml,
  Sparkles,
  Layers,
  Lock
} from 'lucide-react';

const CURRENT_YEAR = new Date().getFullYear();

function GithubIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" className={className}>
      <path d="M12 .5C5.73.5.5 5.73.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.56v-2.17c-3.2.7-3.88-1.37-3.88-1.37-.53-1.34-1.29-1.7-1.29-1.7-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.23-1.28-5.23-5.69 0-1.26.45-2.29 1.19-3.1-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.79 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.12 3.05.74.81 1.18 1.84 1.18 3.1 0 4.42-2.68 5.39-5.24 5.68.41.36.78 1.07.78 2.16v3.2c0 .31.21.68.8.56A11.5 11.5 0 0 0 23.5 12C23.5 5.73 18.27.5 12 .5Z" />
    </svg>
  );
}

export default function App() {
  const [openFaq, setOpenFaq] = useState<number | null>(0);

  const toggleFaq = (index: number) => {
    setOpenFaq(openFaq === index ? null : index);
  };

  return (
    <div className="min-h-screen bg-[#09090b] text-gray-100 font-sans selection:bg-purple-500 selection:text-white antialiased">
      {/* Navigation Bar */}
      <nav className="sticky top-0 z-50 backdrop-blur-xl bg-[#09090b]/80 border-b border-white/10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-purple-500/30">
              <CodeXml className="w-5 h-5 text-white" />
            </div>
            <span className="font-bold text-lg tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white via-gray-200 to-gray-400">
              OpenCode
            </span>
          </div>

          <div className="hidden md:flex items-center space-x-8 text-sm text-gray-400">
            <a href="#features" className="hover:text-white transition-colors">Features</a>
            <a href="#architecture" className="hover:text-white transition-colors">Architecture</a>
            <a href="#community" className="hover:text-white transition-colors">Community</a>
            <a href="#docs" className="hover:text-white transition-colors">Docs</a>
          </div>

          <div className="flex items-center space-x-4">
            <a href="#signin" className="hidden sm:inline text-sm text-gray-400 hover:text-white transition-colors">Sign In</a>
            <a
              href="#get-started"
              className="relative inline-flex items-center justify-center p-0.5 overflow-hidden text-sm font-medium rounded-full group bg-gradient-to-br from-purple-600 to-indigo-600 group-hover:from-purple-600 group-hover:to-indigo-600 hover:text-white text-white shadow-lg shadow-purple-500/25 transition-all"
            >
              <span className="relative px-5 py-2 transition-all ease-in duration-75 bg-[#09090b] rounded-full group-hover:bg-opacity-0">
                Get Started
              </span>
            </a>
          </div>
        </div>
      </nav>

      {/* Hero Section */}
      <section className="relative pt-20 pb-16 overflow-hidden">
        {/* Glow Effects */}
        <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[300px] bg-purple-600/20 blur-[120px] pointer-events-none rounded-full" />

        <div className="max-w-5xl mx-auto px-4 text-center relative z-10">
          <div className="inline-flex items-center space-x-2 px-3 py-1 rounded-full bg-white/[0.03] border border-white/10 text-xs text-purple-300 mb-6 backdrop-blur-md">
            <Sparkles className="w-3.5 h-3.5 text-purple-400" />
            <span>Introducing OpenCode v2.0 Agentic Engine</span>
          </div>

          <h1 className="text-4xl sm:text-6xl lg:text-7xl font-extrabold tracking-tight text-white mb-6 leading-[1.1]">
            Move beyond AI coding to <span className="bg-clip-text text-transparent bg-gradient-to-r from-purple-400 via-indigo-300 to-violet-500">agentic engineering</span>.
          </h1>

          <p className="text-lg sm:text-xl text-gray-400 max-w-2xl mx-auto mb-10">
            The open-source platform that orchestrates autonomous coding agents, multi-model workflows, and deep repository context in real time.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-16">
            <a
              href="#cloud"
              className="w-full sm:w-auto inline-flex items-center justify-center space-x-2 px-8 py-3.5 rounded-full bg-gradient-to-r from-purple-600 to-indigo-600 text-white font-medium hover:opacity-95 shadow-xl shadow-purple-600/30 transition-all"
            >
              <Cloud className="w-5 h-5" />
              <span>Start Free on the Cloud</span>
            </a>
            <a
              href="https://github.com"
              target="_blank"
              rel="noreferrer"
              className="w-full sm:w-auto inline-flex items-center justify-center space-x-2 px-8 py-3.5 rounded-full bg-white/[0.05] hover:bg-white/[0.08] text-white font-medium border border-white/10 transition-all backdrop-blur-md"
            >
              <GithubIcon className="w-5 h-5" />
              <span>View on GitHub</span>
            </a>
          </div>

          {/* Hero Mockup Preview */}
          <div className="relative mx-auto rounded-3xl p-1 bg-gradient-to-b from-purple-500/30 via-white/5 to-transparent shadow-2xl shadow-purple-950/50">
            <div className="bg-[#0f0f13] rounded-[22px] overflow-hidden border border-white/10">
              <div className="flex items-center justify-between px-4 py-3 bg-black/40 border-b border-white/5">
                <div className="flex items-center space-x-2">
                  <div className="w-3 h-3 rounded-full bg-red-500/80" />
                  <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
                  <div className="w-3 h-3 rounded-full bg-green-500/80" />
                </div>
                <div className="text-xs text-gray-400 font-mono">opencode-workspace — agentic-engine.ts</div>
                <div className="w-12" />
              </div>
              <div className="p-6 text-left font-mono text-sm text-gray-300 space-y-3 bg-[#0a0a0e]">
                <div className="text-purple-400">$ opencode agent run --auto-fix --deep-context</div>
                <div className="text-gray-500">→ Initializing multi-agent supervisor (LangGraph / FastMCP)...</div>
                <div className="text-emerald-400">✔ Indexed 1,420 repository files in 0.8s</div>
                <div className="text-gray-300">→ Executing security review &amp; automated refactoring across 3 services...</div>
                <div className="p-3 rounded-lg bg-purple-950/30 border border-purple-500/20 text-purple-200 text-xs">
                  Agent successfully resolved memory bottleneck in asynchronous microservices queue. PR #104 ready for merge.
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Feature Bento Grid / Zigzag */}
      <section id="features" className="py-24 border-t border-white/5 relative">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-24">
          {/* Row 1 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
            <div className="space-y-6">
              <div className="inline-flex p-2 rounded-xl bg-purple-500/10 border border-purple-500/20 text-purple-400">
                <Cpu className="w-6 h-6" />
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white">
                Multi-model AI orchestration with zero lock-in.
              </h2>
              <p className="text-gray-400 text-base sm:text-lg leading-relaxed">
                Seamlessly route tasks between Claude, GPT-4, and open-weights models through unified asynchronous pipelines. Scale your coding workflows without depending on a single provider.
              </p>
            </div>
            <div className="rounded-2xl p-6 bg-white/[0.02] border border-white/10 backdrop-blur-md shadow-xl">
              <div className="space-y-4 font-mono text-xs">
                <div className="p-3 rounded-lg bg-black/40 border border-white/5 flex items-center justify-between">
                  <span className="text-purple-400">Primary: Claude 3.5 Sonnet</span>
                  <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300">Active</span>
                </div>
                <div className="p-3 rounded-lg bg-black/40 border border-white/5 flex items-center justify-between">
                  <span className="text-gray-400">Fallback: DeepSeek R1</span>
                  <span className="px-2 py-0.5 rounded bg-white/10 text-gray-300">Standby</span>
                </div>
              </div>
            </div>
          </div>

          {/* Row 2 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
            <div className="order-2 lg:order-1 rounded-2xl p-6 bg-white/[0.02] border border-white/10 backdrop-blur-md shadow-xl">
              <div className="space-y-3 font-mono text-xs text-gray-300">
                <div className="text-emerald-400">[Security Scan Passed]</div>
                <div>- Zero vulnerabilities detected in dependency tree</div>
                <div>- AST syntax validation completed</div>
                <div className="text-purple-300">- Memory leak guard active</div>
              </div>
            </div>
            <div className="order-1 lg:order-2 space-y-6">
              <div className="inline-flex p-2 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400">
                <ShieldCheck className="w-6 h-6" />
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white">
                Smart checks for bulletproof code reliability and security.
              </h2>
              <p className="text-gray-400 text-base sm:text-lg leading-relaxed">
                Automated static analysis and agentic peer reviews catch edge cases, race conditions, and vulnerabilities before code ever reaches your main branch.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Built For The Way You Work */}
      <section className="py-20 bg-black/40 border-t border-white/5">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center max-w-3xl mx-auto mb-16">
            <h2 className="text-3xl font-bold text-white mb-4">Built for the way you work</h2>
            <p className="text-gray-400">Engineered with speed, security, and developer ergonomics at its core.</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            <div className="p-8 rounded-2xl bg-white/[0.02] border border-white/10 hover:border-purple-500/40 transition-all">
              <div className="w-10 h-10 rounded-lg bg-purple-500/10 flex items-center justify-center text-purple-400 mb-6">
                <Layers className="w-5 h-5" />
              </div>
              <h3 className="text-lg font-semibold text-white mb-2">Instant Indexing</h3>
              <p className="text-sm text-gray-400 leading-relaxed">Deep vector embeddings map your codebase instantly for context-aware code generation.</p>
            </div>

            <div className="p-8 rounded-2xl bg-white/[0.02] border border-white/10 hover:border-purple-500/40 transition-all">
              <div className="w-10 h-10 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-400 mb-6">
                <Zap className="w-5 h-5" />
              </div>
              <h3 className="text-lg font-semibold text-white mb-2">Real-time Event Streams</h3>
              <p className="text-sm text-gray-400 leading-relaxed">Monitor agent progress and inspect tool outputs live through high-throughput event queues.</p>
            </div>

            <div className="p-8 rounded-2xl bg-white/[0.02] border border-white/10 hover:border-purple-500/40 transition-all">
              <div className="w-10 h-10 rounded-lg bg-violet-500/10 flex items-center justify-center text-violet-400 mb-6">
                <Lock className="w-5 h-5" />
              </div>
              <h3 className="text-lg font-semibold text-white mb-2">Sandbox Isolation</h3>
              <p className="text-sm text-gray-400 leading-relaxed">Execute generated code safely in secure, ephemeral micro-VM environments.</p>
            </div>
          </div>
        </div>
      </section>

      {/* FAQ Section */}
      <section className="py-24 border-t border-white/5">
        <div className="max-w-3xl mx-auto px-4">
          <h2 className="text-3xl font-bold text-center text-white mb-12">Common questions</h2>

          <div className="space-y-4">
            {[
              {
                q: "What is OpenCode?",
                a: "OpenCode is an open-source platform that enables developers to orchestrate autonomous coding agents, multi-model LLMs, and repository context securely."
              },
              {
                q: "How does OpenCode differ from standard AI autocomplete?",
                a: "Instead of suggesting single lines, OpenCode operates agentically—analyzing multi-file architectures, writing entire modules, running tests, and opening pull requests."
              },
              {
                q: "Is OpenCode open-source?",
                a: "Yes! OpenCode is fully open-source under the MIT license, allowing self-hosting and custom modifications."
              },
              {
                q: "Can I connect my own models and API keys?",
                a: "Absolutely. You can plug in your own API keys for Anthropic, OpenAI, Groq, or connect locally hosted models via Ollama."
              }
            ].map((faq, idx) => (
              <div key={idx} className="rounded-xl bg-white/[0.02] border border-white/10 overflow-hidden">
                <button
                  onClick={() => toggleFaq(idx)}
                  className="w-full px-6 py-4 text-left flex items-center justify-between font-medium text-white hover:bg-white/[0.02] transition-colors"
                >
                  <span>{faq.q}</span>
                  {openFaq === idx ? <ChevronUp className="w-5 h-5 text-purple-400" /> : <ChevronDown className="w-5 h-5 text-gray-400" />}
                </button>
                {openFaq === idx && (
                  <div className="px-6 pb-4 text-sm text-gray-400 leading-relaxed">
                    {faq.a}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer CTA Banner */}
      <section className="py-20 px-4">
        <div className="max-w-6xl mx-auto rounded-3xl bg-gradient-to-r from-purple-900/60 via-indigo-900/60 to-violet-900/60 border border-purple-500/30 p-12 text-center relative overflow-hidden shadow-2xl">
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-purple-500/20 via-transparent to-transparent pointer-events-none" />

          <h2 className="text-3xl sm:text-5xl font-extrabold text-white mb-6 relative z-10">
            Build something real in minutes
          </h2>
          <p className="text-gray-300 max-w-xl mx-auto mb-8 relative z-10 text-sm sm:text-base">
            Join thousands of developers scaling their engineering velocity with agentic workflows.
          </p>
          <div className="flex flex-col sm:flex-row items-center justify-center gap-4 relative z-10">
            <a
              href="#get-started"
              className="px-8 py-3.5 rounded-full bg-white text-gray-950 font-semibold hover:bg-gray-100 shadow-lg transition-all"
            >
              Get Started Free
            </a>
            <a
              href="#docs"
              className="px-8 py-3.5 rounded-full bg-black/40 text-white font-medium border border-white/20 hover:bg-black/60 backdrop-blur-md transition-all"
            >
              Read Documentation
            </a>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 border-t border-white/10 text-center text-sm text-gray-500">
        <div className="max-w-7xl mx-auto px-4 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center space-x-2">
            <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-purple-500 to-indigo-600 flex items-center justify-center">
              <CodeXml className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="font-semibold text-gray-300">OpenCode</span>
          </div>
          <div>© {CURRENT_YEAR} OpenCode. Open-source agentic engineering.</div>
          <div className="flex space-x-6">
            <a href="https://github.com" target="_blank" rel="noreferrer" className="hover:text-white transition-colors">GitHub</a>
            <a href="#privacy" className="hover:text-white transition-colors">Privacy</a>
            <a href="#terms" className="hover:text-white transition-colors">Terms</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
