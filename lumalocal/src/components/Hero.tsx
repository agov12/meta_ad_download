"use client";

import { motion } from "framer-motion";
import dynamic from "next/dynamic";

const Globe = dynamic(() => import("./Globe"), { ssr: false });

export default function Hero() {
  return (
    <section className="relative min-h-screen flex items-center overflow-hidden pt-24">
      {/* Background grid */}
      <div className="absolute inset-0 bg-grid opacity-50" />

      {/* Radial gradient overlay */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(59,130,246,0.08)_0%,transparent_70%)]" />

      <div className="relative z-10 mx-auto max-w-7xl px-6 w-full">
        <div className="grid lg:grid-cols-2 gap-12 items-center">
          {/* Left: copy */}
          <div className="flex flex-col gap-8">
            <motion.h1
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, ease: "easeOut" }}
              className="text-4xl sm:text-5xl lg:text-6xl font-bold leading-tight tracking-tight text-white"
            >
              Turn winning ads into{" "}
              <span className="gradient-text">localized creative</span> for
              every market.
            </motion.h1>

            <motion.p
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, delay: 0.15, ease: "easeOut" }}
              className="text-lg sm:text-xl text-gray-400 max-w-xl leading-relaxed"
            >
              Repurpose your best-performing English video ads into Spanish,
              French, German, and more&nbsp;&mdash; with translated captions,
              voiceover, on-screen text, and platform-ready exports.
            </motion.p>

            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, delay: 0.3, ease: "easeOut" }}
              className="flex flex-col sm:flex-row gap-4"
            >
              <a
                href="#contact"
                className="inline-flex items-center justify-center rounded-full bg-gradient-to-r from-accent-blue to-accent-cyan px-8 py-3.5 text-base font-semibold text-white transition-all duration-200 hover:shadow-lg hover:shadow-accent-blue/25 hover:scale-105"
              >
                Localize an Ad for Free
              </a>
              <a
                href="#how-it-works"
                className="inline-flex items-center justify-center rounded-full border border-white/10 px-8 py-3.5 text-base font-medium text-gray-300 transition-all duration-200 hover:bg-white/5 hover:text-white hover:border-white/20"
              >
                See How It Works
              </a>
            </motion.div>
          </div>

          {/* Right: globe */}
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 1, delay: 0.2, ease: "easeOut" }}
            className="relative h-[400px] sm:h-[500px] lg:h-[600px]"
          >
            <Globe />
          </motion.div>
        </div>
      </div>
    </section>
  );
}
