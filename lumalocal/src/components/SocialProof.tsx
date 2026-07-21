"use client";

import { motion } from "framer-motion";

const items = [
  "Built for paid social teams",
  "Designed for creative agencies",
  "Fast multilingual ad testing",
  "No reshoots required",
];

export default function SocialProof() {
  return (
    <section className="relative py-12 border-y border-white/5">
      <div className="mx-auto max-w-7xl px-6">
        <motion.div
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true, margin: "-50px" }}
          transition={{ duration: 0.6 }}
          className="flex flex-wrap justify-center gap-x-12 gap-y-4"
        >
          {items.map((item) => (
            <div
              key={item}
              className="flex items-center gap-3 text-sm text-gray-500"
            >
              <div className="h-1.5 w-1.5 rounded-full bg-accent-cyan" />
              {item}
            </div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
