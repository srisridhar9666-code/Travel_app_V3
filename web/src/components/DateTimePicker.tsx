import { useEffect, useState } from 'react';
import { Calendar, Clock, Sparkles, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { todayInIndia } from '@/lib/time';

interface DateTimePickerProps {
  id?: string;
  value: string; // YYYY-MM-DDTHH:mm
  onChange: (value: string) => void;
  required?: boolean;
  disabled?: boolean;
  className?: string;
  placeholder?: string;
  minDate?: string; // YYYY-MM-DD
}

const QUICK_TIME_SLOTS = [
  { label: '06:00 AM', time24: '06:00', desc: 'Early Morning' },
  { label: '09:30 AM', time24: '09:30', desc: 'Morning' },
  { label: '02:00 PM', time24: '14:00', desc: 'Afternoon' },
  { label: '06:30 PM', time24: '18:30', desc: 'Evening' },
  { label: '09:30 PM', time24: '21:30', desc: 'Night' },
];

export function DateTimePicker({
  id,
  value,
  onChange,
  required = false,
  disabled = false,
  className,
  minDate,
}: DateTimePickerProps) {
  // Parse incoming value into date and 24h time
  const [datePart, setDatePart] = useState('');
  const [hour12, setHour12] = useState('10');
  const [minute, setMinute] = useState('30');
  const [ampm, setAmpm] = useState<'AM' | 'PM'>('AM');

  useEffect(() => {
    if (value && value.includes('T')) {
      const [d, t] = value.split('T');
      setDatePart(d || '');
      if (t) {
        const [hStr, mStr] = t.split(':');
        const hNum = parseInt(hStr || '10', 10);
        const m = mStr ? mStr.slice(0, 2) : '00';
        setMinute(m);
        if (hNum >= 12) {
          setAmpm('PM');
          setHour12(hNum === 12 ? '12' : String(hNum - 12).padStart(2, '0'));
        } else {
          setAmpm('AM');
          setHour12(hNum === 0 ? '12' : String(hNum).padStart(2, '0'));
        }
      }
    } else if (value && value.length === 10) {
      setDatePart(value);
    } else if (!value) {
      setDatePart('');
    }
  }, [value]);

  const updateCombined = (d: string, h12: string, m: string, period: 'AM' | 'PM') => {
    if (!d) {
      onChange('');
      return;
    }
    let h24 = parseInt(h12, 10);
    if (period === 'PM' && h24 < 12) h24 += 12;
    if (period === 'AM' && h24 === 12) h24 = 0;
    const h24Str = String(h24).padStart(2, '0');
    const mStr = (m || '00').padStart(2, '0');
    onChange(`${d}T${h24Str}:${mStr}`);
  };

  const handleDateChange = (newDate: string) => {
    setDatePart(newDate);
    updateCombined(newDate, hour12, minute, ampm);
  };

  const handleTimePreset = (time24: string) => {
    const [hStr, mStr] = time24.split(':');
    const hNum = parseInt(hStr, 10);
    const period = hNum >= 12 ? 'PM' : 'AM';
    const h12 = hNum === 0 ? '12' : hNum > 12 ? String(hNum - 12).padStart(2, '0') : String(hNum).padStart(2, '0');
    setHour12(h12);
    setMinute(mStr);
    setAmpm(period);
    const d = datePart || todayInIndia();
    if (!datePart) setDatePart(d);
    updateCombined(d, h12, mStr, period);
  };

  const handleHourChange = (newH: string) => {
    setHour12(newH);
    const d = datePart || todayInIndia();
    if (!datePart) setDatePart(d);
    updateCombined(d, newH, minute, ampm);
  };

  const handleMinuteChange = (newM: string) => {
    setMinute(newM);
    const d = datePart || todayInIndia();
    if (!datePart) setDatePart(d);
    updateCombined(d, hour12, newM, ampm);
  };

  const handleAmpmChange = (newPeriod: 'AM' | 'PM') => {
    setAmpm(newPeriod);
    const d = datePart || todayInIndia();
    if (!datePart) setDatePart(d);
    updateCombined(d, hour12, minute, newPeriod);
  };

  const handleQuickDay = (daysOffset: number) => {
    const target = new Date();
    target.setDate(target.getDate() + daysOffset);
    const yyyy = target.getFullYear();
    const mm = String(target.getMonth() + 1).padStart(2, '0');
    const dd = String(target.getDate()).padStart(2, '0');
    const dStr = `${yyyy}-${mm}-${dd}`;
    handleDateChange(dStr);
  };

  const clear = () => {
    setDatePart('');
    onChange('');
  };

  const todayStr = todayInIndia();

  // Readable preview text
  let formattedDisplay = '';
  if (value && value.includes('T')) {
    try {
      const dt = new Date(value);
      if (!isNaN(dt.getTime())) {
        formattedDisplay = dt.toLocaleDateString('en-IN', {
          weekday: 'short',
          day: '2-digit',
          month: 'short',
          year: 'numeric',
        }) + ' · ' + `${hour12}:${minute} ${ampm}`;
      }
    } catch {
      formattedDisplay = value;
    }
  }

  return (
    <div className={cn('space-y-2 rounded-xl border border-border/80 bg-surface p-3 shadow-2xs', className)}>
      {/* Date row & quick presets */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label htmlFor={id} className="text-2xs font-semibold text-text flex items-center gap-1.5">
            <Calendar size={13} className="text-brand-strong dark:text-brand" />
            <span>Select Travel Date</span>
          </label>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => handleQuickDay(0)}
              className="rounded bg-surface-sunken px-2 py-0.5 text-2xs font-medium text-text-muted hover:bg-brand-soft hover:text-brand-strong transition-colors"
            >
              Today
            </button>
            <button
              type="button"
              onClick={() => handleQuickDay(1)}
              className="rounded bg-surface-sunken px-2 py-0.5 text-2xs font-medium text-text-muted hover:bg-brand-soft hover:text-brand-strong transition-colors"
            >
              Tomorrow
            </button>
            <button
              type="button"
              onClick={() => handleQuickDay(2)}
              className="rounded bg-surface-sunken px-2 py-0.5 text-2xs font-medium text-text-muted hover:bg-brand-soft hover:text-brand-strong transition-colors"
            >
              +2 Days
            </button>
          </div>
        </div>

        <input
          id={id}
          type="date"
          required={required}
          disabled={disabled}
          min={minDate || todayStr}
          value={datePart}
          onChange={(e) => handleDateChange(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm font-medium text-text shadow-2xs transition-colors hover:border-border-strong focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
        />
      </div>

      {/* Time Picker Controls (12-hour + AM/PM) */}
      <div className="space-y-1.5 pt-1 border-t border-border/60">
        <div className="flex items-center justify-between">
          <span className="text-2xs font-semibold text-text flex items-center gap-1.5">
            <Clock size={13} className="text-amber-600 dark:text-amber-400" />
            <span>Time (12-Hour)</span>
          </span>
          <span className="text-2xs text-text-subtle font-mono">
            {hour12}:{minute} {ampm}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* Hour Select */}
          <select
            value={hour12}
            disabled={disabled}
            onChange={(e) => handleHourChange(e.target.value)}
            className="flex-1 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm font-semibold text-text shadow-2xs hover:border-border-strong focus:outline-none focus:ring-1 focus:ring-brand"
          >
            {Array.from({ length: 12 }, (_, i) => {
              const h = String(i + 1).padStart(2, '0');
              return (
                <option key={h} value={h}>
                  {h} hr
                </option>
              );
            })}
          </select>

          <span className="text-base font-bold text-text-subtle">:</span>

          {/* Minute Select */}
          <select
            value={minute}
            disabled={disabled}
            onChange={(e) => handleMinuteChange(e.target.value)}
            className="flex-1 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm font-semibold text-text shadow-2xs hover:border-border-strong focus:outline-none focus:ring-1 focus:ring-brand"
          >
            {['00', '15', '30', '45'].map((m) => (
              <option key={m} value={m}>
                {m} min
              </option>
            ))}
          </select>

          {/* AM / PM Toggle buttons */}
          <div className="flex rounded-lg border border-border bg-surface-sunken p-0.5 shadow-2xs">
            <button
              type="button"
              disabled={disabled}
              onClick={() => handleAmpmChange('AM')}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-bold transition-all',
                ampm === 'AM'
                  ? 'bg-brand text-white shadow-xs'
                  : 'text-text-muted hover:text-text'
              )}
            >
              AM
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => handleAmpmChange('PM')}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-bold transition-all',
                ampm === 'PM'
                  ? 'bg-brand text-white shadow-xs'
                  : 'text-text-muted hover:text-text'
              )}
            >
              PM
            </button>
          </div>
        </div>

        {/* Quick Time Presets */}
        <div className="flex flex-wrap items-center gap-1 pt-1">
          <span className="text-2xs text-text-subtle mr-0.5">Quick:</span>
          {QUICK_TIME_SLOTS.map((slot) => {
            const isSelected =
              ampm === (parseInt(slot.time24.split(':')[0], 10) >= 12 ? 'PM' : 'AM') &&
              minute === slot.time24.split(':')[1];
            return (
              <button
                key={slot.time24}
                type="button"
                onClick={() => handleTimePreset(slot.time24)}
                title={slot.desc}
                className={cn(
                  'rounded-md border px-2 py-0.5 text-2xs transition-all',
                  isSelected
                    ? 'border-brand bg-brand-soft font-semibold text-brand-strong shadow-2xs'
                    : 'border-border bg-surface text-text-muted hover:border-border-strong hover:text-text'
                )}
              >
                {slot.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Selected Result Confirmation Pill */}
      {formattedDisplay && (
        <div className="mt-2 flex items-center justify-between rounded-lg border border-brand/30 bg-brand-soft/40 px-3 py-1.5 text-xs text-brand-strong">
          <div className="flex items-center gap-1.5 font-medium truncate">
            <Sparkles size={13} className="shrink-0 text-brand" />
            <span className="truncate">{formattedDisplay}</span>
          </div>
          {!required && (
            <button
              type="button"
              onClick={clear}
              className="ml-2 text-text-subtle hover:text-danger rounded p-0.5"
              title="Clear selection"
            >
              <X size={12} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}
