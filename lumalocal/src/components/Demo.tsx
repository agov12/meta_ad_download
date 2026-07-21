"use client";

import { motion } from "framer-motion";

const labels = [
  "Translated captions",
  "Spanish voiceover",
  "Localized on-screen text",
  "Platform-ready export",
];

function VideoMockup({
  title,
  lang,
}: {
  title: string;
  lang: string;
}) {
  return (
    <div className="glass-card rounded-2xl overflow-hidden group">
      {/* Video placeholder */}
      <div className="relative aspect-video bg-gradient-to-br from-gray-900 to-gray-800 flex items-center justify-center">
        {/* Play button */}
        <div className="w-16 h-16 rounded-full border-2 border-white/20 flex items-center justify-center group-hover:border-white/40 transition-colors">
          <svg
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="white"
            className="ml-1 opacity-50 group-hover:opacity-80 transition-opacity"
          >
            <polygon points="5 3 19 12 5 21 5 3" />
          </svg>
        </div>

        {/* Language badge */}
        <div className="absolute top-4 left-4 px-3 py-1 rounded-full text-xs font-medium bg-white/10 backdrop-blur-sm text-white/80">
          {lang}
        </div>
      </div>

      {/* Info */}
      <div className="p-6">
        <h3 className="text-base font-semibold text-white mb-3">{title}</h3>
        {title.includes("Localized") && (
          <div className="flex flex-wrap gap-2">
            {labels.map((label) => (
              <span
                key={label}
                className="inline-flex items-center gap-1.5 text-xs text-gray-400 bg-white/5 rounded-full px-3 py-1"
              >
                <div className="h-1 w-1 rounded-full bg-accent-cyan" />
                {label}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Demo() {
  return (
    <section className="relative py-28">
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-2/3 h-px bg-gradient-to-r from-transparent via-accent-blue/30 to-transparent" />

      <div className="mx-auto max-w-7xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.6 }}
          className="text-center mb-16"
        >
          <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">
            Before and after examples <span className="gradient-text">coming soon.</span>
          </h2>
          <p className="text-lg text-gray-400 max-w-xl mx-auto">
            See how a single English ad becomes a fully localized creative ready
            for international markets.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="grid md:grid-cols-2 gap-8 max-w-4xl mx-auto"
        >
          <VideoMockup title="Original English Ad" lang="EN" />
          <VideoMockup title="Localized Spanish Version" lang="ES" />
        </motion.div>
      </div>
    </section>
  );
}
