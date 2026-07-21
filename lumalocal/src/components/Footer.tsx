export default function Footer() {
  return (
    <footer className="border-t border-white/5 py-12">
      <div className="mx-auto max-w-7xl px-6">
        <div className="flex flex-col sm:flex-row items-center justify-between gap-6">
          <div className="text-lg font-bold tracking-tight text-white">
            Luma<span className="gradient-text">Local</span>
          </div>

          <nav className="flex flex-wrap items-center justify-center gap-8">
            <a
              href="#product"
              className="text-sm text-gray-500 hover:text-white transition-colors"
            >
              Product
            </a>
            <a
              href="#use-cases"
              className="text-sm text-gray-500 hover:text-white transition-colors"
            >
              Use Cases
            </a>
            <a
              href="#contact"
              className="text-sm text-gray-500 hover:text-white transition-colors"
            >
              Contact
            </a>
          </nav>

          <p className="text-xs text-gray-600">
            &copy; 2026 LumaLocal. All rights reserved.
          </p>
        </div>
      </div>
    </footer>
  );
}
