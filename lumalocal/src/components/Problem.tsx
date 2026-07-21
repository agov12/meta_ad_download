"use client";

import { motion } from "framer-motion";

const cards = [
  {
    title: "Slow creative turnaround",
    description:
      "Weeks wasted coordinating translations, edits, and re-exports across fragmented vendor workflows.",
    icon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" />
        <polyline points="12 6 12 12 16 14" />
      </svg>
    ),
  },
  {
    title: "Inconsistent translation quality",
    description:
      "Generic translators miss cultural context, brand tone, and the persuasive nuance that makes ads convert.",
    icon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2L2 7l10 5 10-5-10-5z" />
        <path d="M2 17l10 5 10-5" />
        <path d="M2 12l10 5 10-5" />
      </svg>
    ),
  },
  {
    title: "Hard-to-test international markets",
    description:
      "Without fast, affordable localized creative, testing new geos is too expensive to even start.",
    icon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" />
        <line x1="2" y1="12" x2="22" y2="12" />
        <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
      </svg>
    ),
  },
];

const container = {
  hidden: {},
  show: { transition: { staggerChildren: 0.12 } },
};

const item = {
  hidden: { opacity: 0, y: 24 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: "easeOut" as const } },
};

export default function Problem() {
  return (
    <section className="relative py-28">
      <div className="mx-auto max-w-7xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.6 }}
          className="max-w-2xl mb-16"
        >
          <h2 className="text-3xl sm:text-4xl font-bold text-white mb-6 leading-tight">
            Your best ads shouldn&apos;t stop at one language.
          </h2>
          <p className="text-lg text-gray-400 leading-relaxed">
            Brands spend weeks finding creatives that convert. But when it&apos;s
            time to reach new audiences, localization often means slow
            translations, disconnected vendors, awkward subtitles, and expensive
            reshoots.
          </p>
        </motion.div>

        <motion.div
          variants={container}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: "-80px" }}
          className="grid md:grid-cols-3 gap-6"
        >
          {cards.map((card) => (
            <motion.div
              key={card.title}
              variants={item}
              className="glass-card rounded-2xl p-8 transition-all duration-300"
            >
              <div className="text-accent-cyan mb-5">{card.icon}</div>
              <h3 className="text-lg font-semibold text-white mb-3">
                {card.title}
              </h3>
              <p className="text-sm text-gray-400 leading-relaxed">
                {card.description}
              </p>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
