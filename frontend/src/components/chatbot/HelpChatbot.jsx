import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { MessageCircle, X, Send, Sparkles, Compass } from 'lucide-react';
import { getChatStatus, sendChatMessage } from '../../services/api';
import { useTranslation } from '../../i18n';

// Each chip is shown in the active language (labelKey) but sends its original
// English question (q): the backend's built-in guide matches English
// keywords, so the request payload is unchanged.
const SUGGESTIONS = [
  { labelKey: 'chat.sugg.1', q: 'What does the risk score mean?' },
  { labelKey: 'chat.sugg.2', q: 'How do I find high-priority works?' },
  { labelKey: 'chat.sugg.3', q: 'How do I compare two MPs?' },
  { labelKey: 'chat.sugg.4', q: 'What is this platform?' },
  { labelKey: 'chat.sugg.5', q: 'Who is my MP?' },
];

// Constituency/MP replies may carry links to existing pages; only in-app paths are rendered.
const isAppPath = (href) => typeof href === 'string' && href.startsWith('/') && !href.startsWith('//');

export default function HelpChatbot() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [configured, setConfigured] = useState(null); // null = unknown yet
  const [statusFailed, setStatusFailed] = useState(false);
  // The greeting and the error notice are stored as `kind` markers and
  // rendered through t(), so they follow the language selector live. Real
  // conversation turns keep their text as typed / as returned by the backend.
  const [messages, setMessages] = useState([{ role: 'assistant', kind: 'greeting' }]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [everOpened, setEverOpened] = useState(false);
  // Follow-up context from the last reply (e.g. "compare with ..."), sent once with the next message.
  const [pendingContext, setPendingContext] = useState(null);
  const scrollRef = useRef(null);

  const textOf = useCallback((m) => {
    if (m.kind === 'greeting') return t('chat.greeting');
    if (m.kind === 'error') return t('chat.error');
    return m.content;
  }, [t]);

  useEffect(() => {
    // A failed status call is "status unavailable", not "not configured".
    getChatStatus().then((s) => setConfigured(!!s.configured)).catch(() => setStatusFailed(true));
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, sending, open]);

  const openPanel = () => { setOpen(true); setEverOpened(true); };

  const submit = useCallback(async (text, apiText) => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    const nextHistory = [...messages, { role: 'user', content: trimmed }];
    setMessages(nextHistory);
    setInput('');
    setSending(true);
    try {
      const history = messages.slice(-8).map((m) => ({ role: m.role, content: textOf(m) }));
      const context = pendingContext;
      setPendingContext(null);
      const res = await sendChatMessage(apiText || trimmed, history, context);
      setMessages((prev) => [...prev, {
        role: 'assistant',
        content: res.reply,
        links: Array.isArray(res.links) ? res.links.filter((l) => isAppPath(l.href)) : [],
        suggestions: Array.isArray(res.suggestions) ? res.suggestions : [],
      }]);
      setPendingContext(res.context || null);
      if (res.source === 'gemini') setConfigured(true);
    } catch (e) {
      setMessages((prev) => [...prev, { role: 'assistant', kind: 'error' }]);
    } finally {
      setSending(false);
    }
  }, [messages, sending, textOf, pendingContext]);

  const last = messages[messages.length - 1];
  const replyChips = !sending && last && last.role === 'assistant' ? (last.suggestions || []) : [];

  return (
    <>
      <button
        className={`chatbot-fab ${everOpened ? '' : 'invite'}`}
        onClick={() => (open ? setOpen(false) : openPanel())}
        aria-label={open ? t('chat.close') : t('chat.open')}
        aria-expanded={open}
      >
        {open ? <X size={22} /> : <MessageCircle size={22} />}
      </button>

      {open && (
        <div className="chatbot-panel" role="dialog" aria-label={t('chat.dialog')}>
          <div className="chatbot-header">
            <div className="chatbot-header-id">
              <div className="chatbot-avatar"><Compass size={16} /></div>
              <div>
                <div className="chatbot-title">{t('chat.title')}</div>
                <div className={`chatbot-status ${configured ? 'ai' : ''}`}>
                  <span className="dot" />
                  {statusFailed && configured === null ? t('chat.statusFailed') : configured === null ? t('chat.connecting') : configured ? t('chat.ai') : t('chat.guide')}
                </div>
              </div>
            </div>
            <button className="chatbot-close" onClick={() => setOpen(false)} aria-label={t('chat.closeShort')}>
              <X size={16} />
            </button>
          </div>

          <div className="chatbot-messages" ref={scrollRef}>
            {messages.map((m, i) => (
              <div key={i} className={`chatbot-bubble ${m.role}`}>
                {textOf(m)}
                {m.links && m.links.length > 0 && (
                  <div className="chatbot-links">
                    {m.links.map((l) => (
                      <Link key={l.href} to={l.href}>{l.label}</Link>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {sending && (
              <div className="chatbot-bubble assistant chatbot-typing">
                <span /><span /><span />
              </div>
            )}
          </div>

          {messages.length <= 1 && (
            <div className="chatbot-suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s.labelKey} onClick={() => submit(t(s.labelKey), s.q)}>{t(s.labelKey)}</button>
              ))}
            </div>
          )}

          {replyChips.length > 0 && (
            <div className="chatbot-suggestions">
              {replyChips.map((s, i) => (
                <button key={`${i}-${s.message}`} onClick={() => submit(s.label, s.message)}>{s.label}</button>
              ))}
            </div>
          )}

          <form
            className="chatbot-input-row"
            onSubmit={(e) => { e.preventDefault(); submit(input); }}
          >
            <input
              type="text"
              placeholder={t('chat.placeholder')}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={sending}
            />
            <button type="submit" disabled={sending || !input.trim()} aria-label={t('chat.send')}>
              <Send size={15} />
            </button>
          </form>
          <div className="chatbot-footnote">
            <Sparkles size={11} /> {t('chat.footnote')}
          </div>
        </div>
      )}
    </>
  );
}
