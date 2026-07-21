"use client";

import { motion } from "framer-motion";

export default function FinalCTA() {
  return (
    <section id="contact" className="relative py-28">
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-2/3 h-px bg-gradient-to-r from-transparent via-accent-purple/30 to-transparent" />

      {/* Background glow */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(139,92,246,0.06)_0%,transparent_60%)]" />

      <div className="relative mx-auto max-w-3xl px-6 text-center">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.6 }}
        >
          <h2 className="text-3xl sm:text-4xl lg:text-5xl font-bold text-white mb-6 leading-tight">
            Want to see your ad{" "}
            <span className="gradient-text">localized?</span>
          </h2>
          <p className="text-lg text-gray-400 mb-10 leading-relaxed max-w-xl mx-auto">
            Send us one public English video ad. We&apos;ll create a
            Spanish-localized sample so you can see what global creative testing
            could look like.
          </p>
          <a
            href="mailto:hello@lumalocal.com"
            className="inline-flex items-center justify-center rounded-full px-10 py-4 text-base font-semibold text-white transition-all duration-200 hover:scale-105 animated-gradient-border hover:shadow-lg hover:shadow-accent-blue/20"
          >
            Get a Free Sample
          </a>
          <p className="mt-6 text-sm text-gray-500">
            No commitment. No credit card. Just one sample ad.
          </p>
        </motion.div>
      </div>
    </section>
  );
}
