import React, { useState } from 'react';
import { X, Shield, Loader2, Check, Skull, Pencil, Target } from 'lucide-react';

function formatPrice(price) {
  if (price == null) return '';
  const num = Number(price);
  if (num === 0) return '0';
  if (num >= 1000) return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (num >= 1) return num.toFixed(4);
  const str = num.toFixed(20);
  const afterDot = str.split('.')[1] || '';
  let leadingZeros = 0;
  for (const ch of afterDot) { if (ch === '0') leadingZeros++; else break; }
  return num.toFixed(Math.min(leadingZeros + 4, 18));
}

export default function PositionActions({ position, onAction }) {
  const [loading, setLoading] = useState(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const [slInput, setSlInput] = useState('');
  const [slError, setSlError] = useState('');
  const [showSlInput, setShowSlInput] = useState(false);
  const [tpEditLevel, setTpEditLevel] = useState(null); // 'tp1' | 'tp2' | null
  const [tpInput, setTpInput] = useState('');
  const [tpError, setTpError] = useState('');

  const pos = position;
  const isLong = pos.side?.toLowerCase() === 'long' || pos.side?.toLowerCase() === 'buy';

  const handleAction = async (action, params = {}) => {
    const key = action === 'partial_close' ? `partial_${params.percent}` :
                action === 'cancel_tp' ? `cancel_${params.level}` :
                action === 'modify_tp' ? `modify_${params.level}` :
                action;
    setLoading(key);
    try {
      await onAction(action, pos.position_id, params);
    } finally {
      setLoading(null);
      setConfirmClose(false);
    }
  };

  const validateSlPrice = (price) => {
    const mark = Number(pos.mark_price);
    if (!mark || isNaN(mark)) return null;
    if (isLong && price >= mark) return 'SL must be below mark price for longs';
    if (!isLong && price <= mark) return 'SL must be above mark price for shorts';
    return null;
  };

  const validateTpPrice = (price) => {
    const mark = Number(pos.mark_price);
    if (!mark || isNaN(mark)) return null;
    if (isLong && price <= mark) return 'TP must be above mark price for longs';
    if (!isLong && price >= mark) return 'TP must be below mark price for shorts';
    return null;
  };

  const handleModifySl = async () => {
    const price = parseFloat(slInput);
    if (isNaN(price) || price <= 0) { setSlError('Enter a valid price'); return; }
    const err = validateSlPrice(price);
    if (err) { setSlError(err); return; }
    setSlError('');
    setLoading('modify_sl');
    try {
      await onAction('modify_sl', pos.position_id, { price });
      setShowSlInput(false);
      setSlInput('');
    } finally {
      setLoading(null);
    }
  };

  const handleModifyTp = async () => {
    const price = parseFloat(tpInput);
    if (isNaN(price) || price <= 0) { setTpError('Enter a valid price'); return; }
    const err = validateTpPrice(price);
    if (err) { setTpError(err); return; }
    setTpError('');
    const level = tpEditLevel;
    setLoading(`modify_${level}`);
    try {
      await onAction('modify_tp', pos.position_id, { level, price });
      setTpEditLevel(null);
      setTpInput('');
    } finally {
      setLoading(null);
    }
  };

  const tp1State = pos.tp1_closed ? 'hit' : pos.tp1_cancelled ? 'off' : 'active';
  const tp2State = pos.tp2_closed ? 'hit' : pos.tp2_cancelled ? 'off' : 'active';

  const isLoading = (key) => loading === key;
  const BtnSpinner = () => <Loader2 className="w-3 h-3 animate-spin" />;

  const btnBase = "text-xs px-3 py-1.5 rounded-md border transition-all disabled:opacity-30 flex items-center gap-1.5 whitespace-nowrap active:scale-[0.97]";

  const openTpEdit = (level) => {
    const currentPrice = level === 'tp1' ? pos.take_profit_1 : pos.take_profit_2;
    setTpInput(currentPrice ? formatPrice(currentPrice) : '');
    setTpError('');
    setTpEditLevel(level);
  };

  const renderTpBtn = (state, level, label) => {
    if (state === 'hit') {
      return (
        <span className={`${btnBase} bg-emerald-500/10 text-emerald-400/60 border-emerald-500/20 cursor-default`}>
          <Check className="w-3 h-3" /> {label} Hit
        </span>
      );
    }
    // 'off' or 'active' — show Edit + Cancel (or just Edit if off)
    return (
      <div className="flex items-center gap-1">
        <button
          onClick={() => openTpEdit(level)}
          disabled={loading}
          className={`${btnBase} bg-emerald-500/10 text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/20 hover:border-emerald-500/40`}
        >
          <Pencil className="w-3 h-3" />
          {label}
        </button>
        {state === 'active' && (
          <button
            onClick={() => handleAction('cancel_tp', { level })}
            disabled={loading}
            className={`${btnBase} bg-amber-500/10 text-amber-400 border-amber-500/20 hover:bg-amber-500/20 hover:border-amber-500/40`}
          >
            {isLoading(`cancel_${level}`) ? <BtnSpinner /> : <X className="w-3 h-3" />}
          </button>
        )}
      </div>
    );
  };

  const renderTpEditInput = () => {
    if (!tpEditLevel) return null;
    const label = tpEditLevel.toUpperCase();
    return (
      <>
        <div className="flex flex-col">
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-emerald-400/60 font-medium">{label}</span>
            <input
              type="text"
              value={tpInput}
              onChange={(e) => { setTpInput(e.target.value); setTpError(''); }}
              onKeyDown={(e) => e.key === 'Enter' && handleModifyTp()}
              placeholder={`${label} price`}
              autoFocus
              className={`w-28 text-[11px] px-2 py-1 rounded-md bg-black border text-white font-mono focus:outline-none ${tpError ? 'border-emerald-500/80' : 'border-emerald-500/30 focus:border-emerald-500/60'}`}
            />
          </div>
          {tpError && <span className="text-[9px] text-rose-400 mt-0.5 max-w-[9rem] leading-tight">{tpError}</span>}
        </div>
        <button
          onClick={handleModifyTp}
          disabled={loading || !tpInput}
          className={`${btnBase} bg-emerald-500/20 text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/30`}
        >
          {isLoading(`modify_${tpEditLevel}`) ? <BtnSpinner /> : <Check className="w-3 h-3" />}
        </button>
        <button
          onClick={() => { setTpEditLevel(null); setTpInput(''); setTpError(''); }}
          className="p-1.5 text-white/30 hover:text-white/60 transition-colors rounded"
        >
          <X className="w-3 h-3" />
        </button>
      </>
    );
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
      {/* TP buttons */}
      {renderTpBtn(tp1State, 'tp1', 'TP1')}
      {renderTpBtn(tp2State, 'tp2', 'TP2')}

      {/* TP edit input (inline, appears after TP buttons) */}
      {renderTpEditInput()}

      {/* Divider */}
      <span className="w-px h-5 bg-white/10 mx-0.5" />

      {/* Modify SL */}
      {!showSlInput ? (
        <button
          onClick={() => {
            setSlInput(pos.trailing_sl ? formatPrice(pos.trailing_sl) : '');
            setShowSlInput(true);
          }}
          disabled={loading}
          className={`${btnBase} bg-rose-500/10 text-rose-400 border-rose-500/20 hover:bg-rose-500/20 hover:border-rose-500/40`}
        >
          <Shield className="w-3 h-3" />
          SL
        </button>
      ) : (
        <>
          <div className="flex flex-col">
            <input
              type="text"
              value={slInput}
              onChange={(e) => { setSlInput(e.target.value); setSlError(''); }}
              onKeyDown={(e) => e.key === 'Enter' && handleModifySl()}
              placeholder="SL price"
              autoFocus
              className={`w-28 text-[11px] px-2 py-1 rounded-md bg-black border text-white font-mono focus:outline-none ${slError ? 'border-rose-500/80' : 'border-rose-500/30 focus:border-rose-500/60'}`}
            />
            {slError && <span className="text-[9px] text-rose-400 mt-0.5 max-w-[7rem] leading-tight">{slError}</span>}
          </div>
          <button
            onClick={handleModifySl}
            disabled={loading || !slInput}
            className={`${btnBase} bg-rose-500/20 text-rose-400 border-rose-500/30 hover:bg-rose-500/30`}
          >
            {isLoading('modify_sl') ? <BtnSpinner /> : <Check className="w-3 h-3" />}
          </button>
          <button
            onClick={() => { setShowSlInput(false); setSlInput(''); setSlError(''); }}
            className="p-1.5 text-white/30 hover:text-white/60 transition-colors rounded"
          >
            <X className="w-3 h-3" />
          </button>
        </>
      )}

      {/* Divider */}
      <span className="w-px h-5 bg-white/10 mx-0.5" />

      {/* Partial close */}
      {[25, 50, 75].map((pct) => (
        <button
          key={pct}
          onClick={() => handleAction('partial_close', { percent: pct })}
          disabled={loading}
          className={`${btnBase} bg-blue-500/10 text-blue-400 border-blue-500/20 hover:bg-blue-500/20 hover:border-blue-500/40 font-mono`}
        >
          {isLoading(`partial_${pct}`) ? <BtnSpinner /> : null}
          {pct}%
        </button>
      ))}

      {/* Divider */}
      <span className="w-px h-5 bg-white/10 mx-0.5" />

      {/* Full close */}
      {!confirmClose ? (
        <button
          onClick={() => setConfirmClose(true)}
          disabled={loading}
          className={`${btnBase} bg-rose-500/10 text-rose-400 border-rose-500/20 hover:bg-rose-500/20 hover:border-rose-500/40 font-medium`}
        >
          <Skull className="w-3 h-3" />
          Close
        </button>
      ) : (
        <>
          <button
            onClick={() => handleAction('close')}
            disabled={loading}
            className={`${btnBase} bg-rose-500/30 text-white border-rose-500/40 hover:bg-rose-500/50 font-medium`}
          >
            {isLoading('close') ? <BtnSpinner /> : <Skull className="w-3 h-3" />}
            Confirm
          </button>
          <button
            onClick={() => setConfirmClose(false)}
            className={`${btnBase} bg-white/5 text-white/40 border-white/10 hover:bg-white/10`}
          >
            No
          </button>
        </>
      )}
    </div>
  );
}
