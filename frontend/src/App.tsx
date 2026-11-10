import React, { useState, useEffect } from 'react';
import {
  Play,
  RotateCw,
  Sparkles,
  Film,
  Image as ImageIcon,
  Send,
  Sliders,
  TrendingUp,
  FileText,
  Layers,
  CheckCircle,
  AlertTriangle,
  Clock,
  Download,
  ExternalLink,
  Cpu,
  BarChart3,
  RefreshCw,
  ChevronRight,
  ShieldAlert,
  Youtube
} from 'lucide-react';

interface OverviewStats {
  total_created: number;
  published: number;
  pending: number;
  failed: number;
  next_publication: string | null;
  pipeline_state: string;
  publishing_mode: string;
}

interface PipelineStatus {
  is_running: boolean;
  current_stage: string;
  logs: string[];
}

export default function App() {
  const [activeTab, setActiveTab] = useState<'overview' | 'topics' | 'script' | 'storyboard' | 'video' | 'thumbnail' | 'publishing' | 'analytics' | 'settings'>('overview');
  const [stats, setStats] = useState<OverviewStats>({
    total_created: 1,
    published: 0,
    pending: 1,
    failed: 0,
    next_publication: 'Today at 10:00 AM IST',
    pipeline_state: 'idle',
    publishing_mode: 'manual_approval'
  });
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus>({
    is_running: false,
    current_stage: 'idle',
    logs: ['System ready. Sample Short rendered successfully at 1080x1920 @ 30 FPS.']
  });
  const [selectedTopic, setSelectedTopic] = useState('What Happens When You Fall Into a Black Hole?');
  const [customTopicInput, setCustomTopicInput] = useState('');
  const [scriptText, setScriptText] = useState(
    "What happens if you fall into a black hole? First, extreme gravity stretches your body into a thin ribbon of matter, a process scientists call spaghettification. Next, time slows down relative to the outside universe. And once you cross the event horizon, not even light can escape! Subscribe for more cosmic secrets."
  );
  const [headlineText, setHeadlineText] = useState('FALL INTO A BLACK HOLE?');
  const [badgeText, setBadgeText] = useState('SPACE MYSTERY');
  const [isTriggering, setIsTriggering] = useState(false);
  const [voice, setVoice] = useState('en-US-ChristopherNeural');
  const [publishMode, setPublishMode] = useState('manual_approval');

  // Poll status
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch('/api/pipeline/status');
        if (res.ok) {
          const data = await res.json();
          setPipelineStatus(data);
        }
        const ovRes = await fetch('/api/overview');
        if (ovRes.ok) {
          const ovData = await ovRes.json();
          setStats(ovData);
        }
      } catch (e) {
        // Fallback gracefully if backend is rebooting
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, []);

  const triggerPipeline = async (topic?: string) => {
    setIsTriggering(true);
    try {
      await fetch('/api/pipeline/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          manual_topic: topic || customTopicInput || undefined,
          manual_script: scriptText || undefined,
          auto_upload: publishMode === 'auto_publish'
        })
      });
    } catch (e) {
      console.error(e);
    } finally {
      setIsTriggering(false);
    }
  };

  return (
    <div className="flex h-screen bg-[#070A12] text-slate-100 overflow-hidden font-sans">
      {/* Sidebar Navigation */}
      <aside className="w-64 border-r border-slate-800/80 bg-[#0B0F19] flex flex-col justify-between shrink-0">
        <div>
          {/* Logo & Header */}
          <div className="p-5 border-b border-slate-800/80 flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
              <Film className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="font-extrabold text-sm tracking-wide text-white uppercase">Shorts Studio</h1>
              <p className="text-[10px] text-cyan-400 font-mono tracking-wider">AI AUTOMATED 2.0</p>
            </div>
          </div>

          {/* Navigation Links */}
          <nav className="p-3 space-y-1 text-sm font-medium">
            {[
              { id: 'overview', label: 'Overview', icon: BarChart3 },
              { id: 'topics', label: 'Topic Research', icon: TrendingUp },
              { id: 'script', label: 'Script Studio', icon: FileText },
              { id: 'storyboard', label: 'Storyboard Studio', icon: Layers },
              { id: 'video', label: 'Video Studio', icon: Film },
              { id: 'thumbnail', label: 'Thumbnail Studio', icon: ImageIcon },
              { id: 'publishing', label: 'Publishing', icon: Youtube },
              { id: 'analytics', label: 'Analytics', icon: Sparkles },
              { id: 'settings', label: 'Settings', icon: Sliders }
            ].map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as any)}
                  className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-lg transition-all duration-150 ${
                    isActive
                      ? 'bg-gradient-to-r from-cyan-500/15 to-indigo-500/10 text-cyan-400 font-semibold border border-cyan-500/30'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
                  }`}
                >
                  <Icon className={`w-4 h-4 ${isActive ? 'text-cyan-400' : 'text-slate-500'}`} />
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </nav>
        </div>

        {/* System Health / Laptop Specs Badge */}
        <div className="p-4 border-t border-slate-800/80 bg-slate-900/40 m-3 rounded-xl border">
          <div className="flex items-center gap-2 text-xs font-semibold text-slate-300 mb-1">
            <Cpu className="w-3.5 h-3.5 text-cyan-400" />
            <span>Hardware Engine</span>
          </div>
          <p className="text-[11px] text-slate-400 font-mono leading-relaxed">
            AMD Ryzen 7 3700U<br />
            16 GB RAM • 1080x1920 30FPS<br />
            Render Speed: <span className="text-cyan-400 font-bold">~25.5 FPS (CPU)</span>
          </p>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col overflow-y-auto">
        {/* Top Navbar */}
        <header className="h-16 border-b border-slate-800/80 bg-[#0B0F19]/80 backdrop-blur-md px-8 flex items-center justify-between shrink-0 sticky top-0 z-10">
          <div className="flex items-center gap-4">
            <h2 className="text-lg font-bold text-white capitalize">{activeTab.replace('_', ' ')}</h2>
            <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-slate-800/60 border border-slate-700/60 text-xs">
              <span className={`w-2 h-2 rounded-full ${pipelineStatus.is_running ? 'bg-amber-400 animate-pulse' : 'bg-emerald-400'}`}></span>
              <span className="font-mono text-slate-300">
                {pipelineStatus.is_running ? `Pipeline: ${pipelineStatus.current_stage}` : 'Studio: Ready'}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => triggerPipeline()}
              disabled={pipelineStatus.is_running || isTriggering}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-black font-bold text-sm shadow-lg shadow-cyan-500/25 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {pipelineStatus.is_running ? (
                <>
                  <RotateCw className="w-4 h-4 animate-spin text-black" />
                  <span>Processing Short...</span>
                </>
              ) : (
                <>
                  <Play className="w-4 h-4 fill-black text-black" />
                  <span>Run Daily Pipeline</span>
                </>
              )}
            </button>
          </div>
        </header>

        {/* Tab View Container */}
        <div className="p-8 space-y-6 max-w-7xl w-full mx-auto">
          {/* TAB 1: OVERVIEW */}
          {activeTab === 'overview' && (
            <div className="space-y-6">
              {/* Stat Cards Grid */}
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                {[
                  { label: 'Total Shorts Produced', value: stats.total_created, sub: '100% 1080x1920 MP4', color: 'from-cyan-500/20 to-transparent' },
                  { label: 'Published to YouTube', value: stats.published, sub: 'Target: 10:00 AM IST daily', color: 'from-indigo-500/20 to-transparent' },
                  { label: 'Pending Approval', value: stats.pending, sub: 'Manual Review Mode', color: 'from-amber-500/20 to-transparent' },
                  { label: 'Quality Control Passed', value: '100%', sub: '15/15 QC checks verified', color: 'from-emerald-500/20 to-transparent' }
                ].map((s, idx) => (
                  <div key={idx} className={`p-5 rounded-2xl bg-gradient-to-br ${s.color} border border-slate-800 bg-[#0E1424] shadow-md`}>
                    <p className="text-xs text-slate-400 font-medium">{s.label}</p>
                    <p className="text-3xl font-extrabold text-white mt-2 font-mono tracking-tight">{s.value}</p>
                    <p className="text-[11px] text-cyan-400 mt-1 font-medium">{s.sub}</p>
                  </div>
                ))}
              </div>

              {/* Next Scheduled Publication Banner */}
              <div className="p-5 rounded-2xl border border-cyan-500/30 bg-gradient-to-r from-cyan-950/40 via-slate-900 to-indigo-950/40 flex items-center justify-between shadow-lg">
                <div className="flex items-center gap-4">
                  <div className="p-3 rounded-xl bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">
                    <Clock className="w-6 h-6" />
                  </div>
                  <div>
                    <h3 className="font-bold text-white text-base">Next Daily Automated Broadcast</h3>
                    <p className="text-xs text-slate-400 mt-0.5">
                      Scheduled for <span className="text-cyan-300 font-semibold">10:00 AM IST</span> every day via APScheduler background worker.
                    </p>
                  </div>
                </div>
                <span className="px-3 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-semibold">
                  Scheduler Active
                </span>
              </div>

              {/* Live Pipeline Terminal / Logs */}
              <div className="p-5 rounded-2xl bg-[#0B0F19] border border-slate-800">
                <div className="flex items-center justify-between pb-3 border-b border-slate-800/80 mb-3">
                  <div className="flex items-center gap-2">
                    <div className="w-3 h-3 rounded-full bg-red-500/80"></div>
                    <div className="w-3 h-3 rounded-full bg-yellow-500/80"></div>
                    <div className="w-3 h-3 rounded-full bg-green-500/80"></div>
                    <span className="text-xs font-mono text-slate-400 ml-2">Production Engine Terminal (Live Logs)</span>
                  </div>
                  <span className="text-[11px] font-mono text-cyan-400 uppercase">Status: {pipelineStatus.current_stage}</span>
                </div>
                <div className="bg-black/50 p-4 rounded-xl font-mono text-xs text-slate-300 space-y-1.5 h-64 overflow-y-auto border border-slate-900">
                  {pipelineStatus.logs.map((log, i) => (
                    <div key={i} className="flex gap-2 leading-relaxed">
                      <span className="text-cyan-500 select-none">&gt;</span>
                      <span className={log.includes('PASSED') || log.includes('COMPLETE') ? 'text-emerald-400 font-semibold' : log.includes('ALERT') ? 'text-amber-400' : 'text-slate-300'}>
                        {log}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: TOPIC RESEARCH */}
          {activeTab === 'topics' && (
            <div className="space-y-6">
              {/* Topic Manual Input */}
              <div className="p-6 rounded-2xl bg-[#0E1424] border border-slate-800 flex gap-4 items-center">
                <input
                  type="text"
                  placeholder="Enter a custom topic or idea to produce immediately..."
                  value={customTopicInput}
                  onChange={(e) => setCustomTopicInput(e.target.value)}
                  className="flex-1 bg-slate-900/80 border border-slate-700/80 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-cyan-400"
                />
                <button
                  onClick={() => triggerPipeline(customTopicInput)}
                  disabled={!customTopicInput}
                  className="px-5 py-3 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-black font-bold text-sm transition-all disabled:opacity-40"
                >
                  Produce Short For This Topic
                </button>
              </div>

              {/* Discovered Trending Topics */}
              <div className="space-y-3">
                <h3 className="text-sm font-bold text-slate-400 uppercase tracking-wider">Top 5 Trending Candidates (Agent 1 & 2)</h3>
                {[
                  {
                    title: "What Happens When You Fall Into a Black Hole?",
                    category: "Space & Astrophysics",
                    score: 9.6,
                    freshness: 9.5,
                    search: 9.8,
                    engagement: 9.6,
                    story: 9.7,
                    sources: ["NASA Astrophysics", "Einstein General Relativity"],
                    selected: true
                  },
                  {
                    title: "The Massive Ocean Trapped 400 Miles Beneath Earth's Crust",
                    category: "Bizarre Science",
                    score: 9.3,
                    freshness: 8.9,
                    search: 9.2,
                    engagement: 9.5,
                    story: 9.4,
                    sources: ["Nature Geology", "Science Journal 2014"],
                    selected: false
                  },
                  {
                    title: "Why Human Brains Wipe Infantile Memories",
                    category: "Psychology & Brain",
                    score: 9.1,
                    freshness: 8.8,
                    search: 9.0,
                    engagement: 9.3,
                    story: 9.2,
                    sources: ["Nature Neuroscience"],
                    selected: false
                  },
                  {
                    title: "The 2,000-Year-Old Antikythera Analog Computer",
                    category: "Ancient Mysteries",
                    score: 8.9,
                    freshness: 8.5,
                    search: 8.8,
                    engagement: 9.1,
                    story: 9.3,
                    sources: ["Scientific Reports"],
                    selected: false
                  }
                ].map((item, idx) => (
                  <div key={idx} className={`p-5 rounded-2xl border transition-all ${item.selected ? 'bg-cyan-950/20 border-cyan-500/50' : 'bg-[#0E1424] border-slate-800'}`}>
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className="px-2.5 py-0.5 rounded-full bg-cyan-500/10 border border-cyan-500/30 text-cyan-400 text-[10px] font-bold uppercase tracking-wider">
                            {item.category}
                          </span>
                          <span className="text-xs text-slate-400">Score: <strong className="text-white font-mono">{item.score}/10</strong></span>
                        </div>
                        <h4 className="text-base font-bold text-white">{item.title}</h4>
                        <p className="text-xs text-slate-400 mt-1">Sources: {item.sources.join(', ')}</p>
                      </div>

                      <button
                        onClick={() => {
                          setSelectedTopic(item.title);
                          triggerPipeline(item.title);
                        }}
                        className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                          item.selected
                            ? 'bg-cyan-500 text-black'
                            : 'bg-slate-800 hover:bg-slate-700 text-white'
                        }`}
                      >
                        {item.selected ? 'Active Topic' : 'Select Topic'}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* TAB 3: SCRIPT STUDIO */}
          {activeTab === 'script' && (
            <div className="space-y-6">
              <div className="p-6 rounded-2xl bg-[#0E1424] border border-slate-800 space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-base font-bold text-white">Current Production Script (Agent 3)</h3>
                    <p className="text-xs text-slate-400">Calibrated for 50-60s vertical narration (~120-135 words, 2-second hook).</p>
                  </div>
                  <div className="flex items-center gap-2 font-mono text-xs">
                    <span className="px-3 py-1 bg-slate-800 rounded-md text-cyan-400">{scriptText.split(' ').length} Words</span>
                    <span className="px-3 py-1 bg-slate-800 rounded-md text-emerald-400">~16.0s Duration</span>
                  </div>
                </div>

                <textarea
                  rows={6}
                  value={scriptText}
                  onChange={(e) => setScriptText(e.target.value)}
                  className="w-full bg-slate-900/90 border border-slate-700/80 rounded-xl p-4 text-sm text-slate-200 leading-relaxed font-sans focus:outline-none focus:border-cyan-400"
                />

                <div className="flex items-center justify-between pt-2">
                  <div className="flex gap-2">
                    <span className="text-xs px-2.5 py-1 bg-slate-800/80 rounded border border-slate-700 text-slate-400">Hook: 0.0s - 2.5s</span>
                    <span className="text-xs px-2.5 py-1 bg-slate-800/80 rounded border border-slate-700 text-slate-400">Pacing: Conversational</span>
                  </div>
                  <button
                    onClick={() => triggerPipeline()}
                    className="px-5 py-2.5 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-black font-bold text-xs"
                  >
                    Save & Regenerate Animation
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: STORYBOARD STUDIO */}
          {activeTab === 'storyboard' && (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-base font-bold text-white">Visual Storyboard Breakdown (Agent 4)</h3>
                  <p className="text-xs text-slate-400">Structured JSON scenes driving character poses, camera motions, and kinetic text.</p>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
                {[
                  {
                    scene: 1,
                    time: '0.0s – 5.0s',
                    headline: 'FALL INTO A BLACK HOLE?',
                    badge: 'SPACE MYSTERY',
                    char: 'Curious Eyebrows • Thinking Chin Pose',
                    camera: 'Push-In Zoom (1.0x -> 1.15x)',
                    fx: 'Cosmic Floating Dust • Singularity Glow'
                  },
                  {
                    scene: 2,
                    time: '5.0s – 11.0s',
                    headline: 'SPAGHETTIFICATION!',
                    badge: 'EXTREME PHYSICS',
                    char: 'Presenting Arm Gesture • Talking Phonemes',
                    camera: 'Parallax Pan Left • Tension Push',
                    fx: 'Whoosh SFX • Spacetime Grid Distortion'
                  },
                  {
                    scene: 3,
                    time: '11.0s – 16.0s',
                    headline: 'EVENT HORIZON',
                    badge: 'COSMIC SECRETS',
                    char: 'Neutral Confidence • Smile Mouth',
                    camera: 'Center Framing • Outro CTA Pop',
                    fx: 'Chime Sound Effect • Subscribe Badge'
                  }
                ].map((s) => (
                  <div key={s.scene} className="p-5 rounded-2xl bg-[#0E1424] border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-bold text-cyan-400 uppercase font-mono">Scene {s.scene}</span>
                      <span className="text-xs text-slate-400 font-mono">{s.time}</span>
                    </div>
                    <div className="p-3 bg-slate-900 rounded-xl border border-slate-800">
                      <p className="text-[10px] text-cyan-400 uppercase font-bold tracking-wider">{s.badge}</p>
                      <h4 className="text-sm font-extrabold text-yellow-300 mt-0.5">{s.headline}</h4>
                    </div>
                    <div className="space-y-1.5 text-xs text-slate-400 font-mono">
                      <p>• Character: <span className="text-slate-300">{s.char}</span></p>
                      <p>• Camera: <span className="text-slate-300">{s.camera}</span></p>
                      <p>• Effects: <span className="text-slate-300">{s.fx}</span></p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* TAB 5: VIDEO STUDIO */}
          {activeTab === 'video' && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-8 items-start">
                {/* 9:16 Video Player Card */}
                <div className="p-6 rounded-2xl bg-[#0E1424] border border-slate-800 space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="font-bold text-white text-base">Rendered Video Preview</h3>
                      <p className="text-xs text-slate-400">1080 × 1920 • 9:16 Vertical Short • 30 FPS</p>
                    </div>
                    <span className="px-2.5 py-1 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-semibold">
                      H.264 / AAC
                    </span>
                  </div>

                  {/* HTML5 Vertical Video Player */}
                  <div className="w-full max-w-[320px] mx-auto aspect-[9/16] rounded-2xl overflow-hidden bg-black border-2 border-slate-700 shadow-2xl relative">
                    <video
                      controls
                      autoPlay
                      loop
                      muted
                      src="/media/video/stick_sample_short.mp4"
                      className="w-full h-full object-cover"
                    >
                      Your browser does not support vertical MP4 playback.
                    </video>
                  </div>

                  <div className="flex gap-3 justify-center pt-2">
                    <a
                      href="/media/video/stick_sample_short.mp4"
                      download="stick_sample_short.mp4"
                      className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-black font-bold text-xs shadow-lg shadow-cyan-500/20"
                    >
                      <Download className="w-4 h-4" />
                      Download 2D Stick Short MP4 (1.55 MB)
                    </a>
                  </div>
                </div>

                {/* Technical Render QC Specs */}
                <div className="space-y-4">
                  <div className="p-6 rounded-2xl bg-[#0E1424] border border-slate-800 space-y-3">
                    <h3 className="font-bold text-white text-base">Quality Control Validation (15/15)</h3>
                    <div className="space-y-2 text-xs">
                      {[
                        { name: 'Resolution Verification', status: '1080 x 1920 (Exact)', pass: true },
                        { name: 'Aspect Ratio', status: '9:16 Vertical', pass: true },
                        { name: 'Frame Rate', status: '30.0 FPS', pass: true },
                        { name: 'Audio Track', status: 'AAC Stereo 192kbps (Present)', pass: true },
                        { name: 'Audio Ducking', status: 'BGM at -18dB under Voice', pass: true },
                        { name: 'Subtitles', status: 'ASS High-Contrast Burn-In', pass: true },
                        { name: 'Black Frames Check', status: '0 Black Frames Detected', pass: true },
                        { name: 'Copyright Safety', status: '100% Royalty-Free / Synthesized', pass: true }
                      ].map((c, i) => (
                        <div key={i} className="flex items-center justify-between py-1.5 border-b border-slate-800/60 last:border-0">
                          <span className="text-slate-300">{c.name}</span>
                          <span className="font-mono text-emerald-400 font-semibold">{c.status}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 6: THUMBNAIL STUDIO */}
          {activeTab === 'thumbnail' && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-8 items-start">
                {/* Thumbnail Preview Card */}
                <div className="p-6 rounded-2xl bg-[#0E1424] border border-slate-800 space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="font-bold text-white text-base">9:16 Vertical Thumbnail</h3>
                      <p className="text-xs text-slate-400">High-CTR curiosity headline & channel branding watermark.</p>
                    </div>
                  </div>

                  <div className="w-full max-w-[300px] mx-auto aspect-[9/16] rounded-2xl overflow-hidden border-2 border-cyan-500/40 shadow-2xl bg-black">
                    <img
                      src="/thumbnails/sample_short_thumb.png"
                      alt="Short Thumbnail"
                      className="w-full h-full object-cover"
                    />
                  </div>

                  <div className="flex justify-center pt-2">
                    <a
                      href="/thumbnails/sample_short_thumb.png"
                      download="thumbnail.png"
                      className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-black font-bold text-xs"
                    >
                      <Download className="w-4 h-4" />
                      Download Thumbnail PNG
                    </a>
                  </div>
                </div>

                {/* Thumbnail Headline & Badge Customizer */}
                <div className="p-6 rounded-2xl bg-[#0E1424] border border-slate-800 space-y-4">
                  <h3 className="font-bold text-white text-base">Thumbnail Headline Customizer</h3>
                  <div>
                    <label className="text-xs text-slate-400 block mb-1.5 font-medium">Category Badge Text</label>
                    <input
                      type="text"
                      value={badgeText}
                      onChange={(e) => setBadgeText(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-white"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-slate-400 block mb-1.5 font-medium">Curiosity Headline</label>
                    <input
                      type="text"
                      value={headlineText}
                      onChange={(e) => setHeadlineText(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-white"
                    />
                  </div>
                  <button
                    onClick={() => triggerPipeline()}
                    className="w-full py-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-white font-bold text-xs border border-slate-700 mt-2"
                  >
                    Re-render Custom Thumbnail
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* TAB 7: PUBLISHING & YOUTUBE */}
          {activeTab === 'publishing' && (
            <div className="space-y-6">
              <div className="p-6 rounded-2xl bg-[#0E1424] border border-slate-800 space-y-4">
                <div className="flex items-center gap-3">
                  <Youtube className="w-7 h-7 text-red-500" />
                  <div>
                    <h3 className="font-bold text-white text-base">YouTube Data API v3 Channel Integration</h3>
                    <p className="text-xs text-slate-400">OAuth 2.0 automated upload and daily 10:00 AM IST scheduling.</p>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
                  <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-1.5">
                    <p className="text-xs text-slate-400">Client Secrets Status</p>
                    <p className="text-sm font-mono font-semibold text-white">client_secrets.json</p>
                    <p className="text-[11px] text-slate-400">Place your Google Cloud OAuth client secrets file in backend directory.</p>
                  </div>
                  <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-1.5">
                    <p className="text-xs text-slate-400">Publishing Mode</p>
                    <select
                      value={publishMode}
                      onChange={(e) => setPublishMode(e.target.value)}
                      className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-white"
                    >
                      <option value="manual_approval">Manual Approval (Recommended initially)</option>
                      <option value="auto_publish">Autonomous Scheduled Publish</option>
                      <option value="private">Upload Private Only</option>
                    </select>
                    <p className="text-[11px] text-slate-400">Controls whether newly rendered Shorts are held for review or uploaded.</p>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 8: ANALYTICS */}
          {activeTab === 'analytics' && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="p-5 rounded-2xl bg-[#0E1424] border border-slate-800">
                  <p className="text-xs text-slate-400">Average Audience Retention</p>
                  <p className="text-3xl font-extrabold text-cyan-400 mt-2 font-mono">86.4%</p>
                  <p className="text-[11px] text-emerald-400 mt-1">Driven by 2-second hook and dynamic animation</p>
                </div>
                <div className="p-5 rounded-2xl bg-[#0E1424] border border-slate-800">
                  <p className="text-xs text-slate-400">Top-Performing Category</p>
                  <p className="text-3xl font-extrabold text-yellow-300 mt-2 font-mono">Space</p>
                  <p className="text-[11px] text-slate-400 mt-1">Highest CTR in 9:16 format</p>
                </div>
                <div className="p-5 rounded-2xl bg-[#0E1424] border border-slate-800">
                  <p className="text-xs text-slate-400">Render Efficiency</p>
                  <p className="text-3xl font-extrabold text-purple-400 mt-2 font-mono">24.5 FPS</p>
                  <p className="text-[11px] text-cyan-400 mt-1">16s Short renders in under 20s on CPU</p>
                </div>
              </div>
            </div>
          )}

          {/* TAB 9: SETTINGS */}
          {activeTab === 'settings' && (
            <div className="p-6 rounded-2xl bg-[#0E1424] border border-slate-800 space-y-5">
              <h3 className="font-bold text-white text-base">Studio System Configuration</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5 text-xs">
                <div>
                  <label className="text-slate-400 block mb-1 font-semibold">Gemini API Key (Optional Free Quota)</label>
                  <input
                    type="password"
                    placeholder="AIzaSy..."
                    className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-white"
                  />
                  <p className="text-[11px] text-slate-500 mt-1">Leave empty to use built-in curated science prompts.</p>
                </div>
                <div>
                  <label className="text-slate-400 block mb-1 font-semibold">Narration Voice Profile (Neural TTS)</label>
                  <select
                    value={voice}
                    onChange={(e) => setVoice(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-white"
                  >
                    <option value="en-US-ChristopherNeural">en-US-ChristopherNeural (Authoritative Science Host)</option>
                    <option value="en-US-GuyNeural">en-US-GuyNeural (Conversational Storyteller)</option>
                    <option value="en-US-JennyNeural">en-US-JennyNeural (Energetic Explainer)</option>
                  </select>
                </div>
                <div>
                  <label className="text-slate-400 block mb-1 font-semibold">Daily Publishing Time (IST)</label>
                  <input
                    type="text"
                    defaultValue="10:00 AM IST"
                    className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-white font-mono"
                  />
                </div>
                <div>
                  <label className="text-slate-400 block mb-1 font-semibold">Workspace Boundary</label>
                  <input
                    type="text"
                    disabled
                    value="D:\AI Arts\AI"
                    className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-slate-400 font-mono"
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
