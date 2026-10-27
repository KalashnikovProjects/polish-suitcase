/** Small safe subset for model emphasis; text is escaped by React, HTML is never injected. */
import { userText } from '@/lib/presentation';
export default function MessageText({ text = '' }: { text?: string }) {
  return <>{userText(text).split(/(\*\*[^*]+\*\*)/g).map((part, index) => part.startsWith('**') && part.endsWith('**') ? <strong key={index}>{part.slice(2, -2)}</strong> : part)}</>;
}
