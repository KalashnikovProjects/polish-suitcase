import Image from 'next/image';

export default function Brand({ compact = false, onGreen = false, className = '' }: { compact?: boolean; onGreen?: boolean; className?: string }) {
  return (
    <div className={`flex min-w-0 items-center gap-2.5 ${className}`}>
      <span className={`flex shrink-0 items-center justify-center rounded-xl border border-slate-700 bg-slate-900 shadow-sm ${compact ? 'h-8 w-8' : 'h-10 w-10'}`}>
        <Image src="/brand-suitcase.svg" alt="" width={compact ? 24 : 30} height={compact ? 26 : 32} />
      </span>
      <span className={`${compact ? 'text-xs' : 'text-sm'} min-w-0 font-extrabold leading-tight tracking-tight ${onGreen ? 'text-[var(--color-brand-navy)]' : 'text-slate-100'}`}>
        <span className={onGreen ? 'text-[var(--color-brand-navy)]' : 'text-blue-600'}>пО!</span>льский чемоданчик
      </span>
    </div>
  );
}
