import type { AvatarId } from '../api';
import type { VoiceState } from '../voiceState';

/** What the character is doing. `happy` is a short celebration after a verified task; `concerned` = waiting / failed. */
export type AvatarMood = 'idle' | 'listening' | 'thinking' | 'speaking' | 'happy' | 'concerned';

export function moodFor(state: VoiceState, justCompleted = false): AvatarMood {
  switch (state) {
    case 'listening':
    case 'transcribing':
      return 'listening';
    case 'thinking':
    case 'executing':
    case 'verifying':
      return 'thinking';
    case 'speaking':
      return 'speaking';
    case 'awaiting_approval':
    case 'awaiting_input':
    case 'error':
      return 'concerned';
    default:
      return justCompleted ? 'happy' : 'idle';
  }
}

type Hair = 'bob' | 'long' | 'ponytail' | 'swept' | 'buzz' | 'curly' | 'none';
interface Look {
  skin: string;
  hair: Hair;
  hairColor: string;
}

// Original characters: nothing here imitates a real person, film or game character.
const LOOKS: Record<AvatarId, Look> = {
  nova: { skin: '#F2C9A5', hair: 'bob', hairColor: '#1B2A41' },
  aria: { skin: '#C98F6B', hair: 'long', hairColor: '#4A2E1B' },
  zara: { skin: '#8D5A3C', hair: 'ponytail', hairColor: '#2A1230' },
  orion: { skin: '#E3B38C', hair: 'swept', hairColor: '#1D2B4F' },
  atlas: { skin: '#B07A55', hair: 'buzz', hairColor: '#2B2F36' },
  kai: { skin: '#6F4630', hair: 'curly', hairColor: '#10261C' },
  custom: { skin: '#D9B99B', hair: 'none', hairColor: '#333' },
};

function HairBack({ hair, color }: { hair: Hair; color: string }) {
  switch (hair) {
    case 'bob':
      return <path d="M58 95 Q55 40 100 38 Q145 40 142 95 L142 122 Q130 127 128 105 L72 105 Q70 127 58 122 Z" fill={color} />;
    case 'long':
      return <path d="M56 90 Q54 38 100 36 Q146 38 144 90 L152 164 Q132 152 130 110 L70 110 Q68 152 48 164 Z" fill={color} />;
    case 'ponytail':
      return <path d="M138 62 Q178 70 166 126 Q160 96 136 82 Z" fill={color} />;
    case 'curly':
      return (
        <g fill={color}>
          <circle cx="64" cy="66" r="16" /><circle cx="84" cy="48" r="17" /><circle cx="116" cy="48" r="17" />
          <circle cx="136" cy="66" r="16" /><circle cx="100" cy="42" r="16" />
        </g>
      );
    default:
      return null;
  }
}

function HairFront({ hair, color }: { hair: Hair; color: string }) {
  switch (hair) {
    case 'bob':
      return <path d="M60 86 Q60 46 100 44 Q140 46 140 86 Q124 62 100 64 Q76 62 60 86 Z" fill={color} />;
    case 'long':
      return <path d="M58 86 Q58 44 100 42 Q142 44 142 86 Q120 58 96 62 Q74 66 58 86 Z" fill={color} />;
    case 'ponytail':
      return <path d="M60 86 Q58 42 100 40 Q142 42 140 86 Q132 62 100 60 Q68 62 60 86 Z" fill={color} />;
    case 'swept':
      return <path d="M60 82 Q58 40 100 38 Q144 40 140 82 Q128 52 92 58 Q70 62 60 82 Z" fill={color} />;
    case 'buzz':
      return <path d="M62 80 Q64 46 100 44 Q136 46 138 80 Q120 64 100 64 Q80 64 62 80 Z" fill={color} opacity="0.85" />;
    case 'curly':
      return <path d="M62 80 Q70 56 100 58 Q130 56 138 80 Q122 68 100 68 Q78 68 62 80 Z" fill={color} />;
    default:
      return null;
  }
}

interface AvatarProps {
  id: AvatarId;
  color: string;
  mood?: AvatarMood;
  /** 0..1 loudness of the voice that is playing; drives the mouth. */
  level?: number;
  size?: number;
  /** Custom avatar: the user's own picture (a data URL kept in this browser only). */
  image?: string | null;
  label?: string;
}

/** An animated 2D character. Every mood is also exposed as text through `aria-label`. */
export function Avatar({ id, color, mood = 'idle', level = 0, size = 160, image, label }: AvatarProps) {
  const look = LOOKS[id];
  const open = mood === 'speaking' ? 1.5 + Math.min(1, level) * 9 : mood === 'listening' ? 2.5 : 1.2;
  const smile = mood === 'happy';
  const brow = mood === 'concerned' ? 5 : mood === 'happy' ? -2 : 0;
  const clip = `av-clip-${id}`;
  return (
    <svg
      viewBox="0 0 200 200"
      width={size}
      height={size}
      role="img"
      aria-label={label ?? `${id} avatar, ${mood}`}
      data-testid="avatar"
      data-mood={mood}
      data-avatar={id}
      className={`avatar avatar-${mood}`}
      style={{ ['--av' as string]: color }}
    >
      <defs>
        <clipPath id={clip}><circle cx="100" cy="100" r="88" /></clipPath>
        <radialGradient id={`av-glow-${id}`} cx="50%" cy="45%" r="60%">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0.04" />
        </radialGradient>
      </defs>
      <circle className="av-ring" cx="100" cy="100" r="95" fill="none" stroke={color} strokeWidth="3" />
      <circle cx="100" cy="100" r="88" fill={`url(#av-glow-${id})`} />
      {mood === 'thinking' && (
        <g className="av-orbit" aria-hidden="true">
          <circle cx="100" cy="14" r="5" fill={color} /><circle cx="186" cy="100" r="4" fill={color} opacity="0.7" />
          <circle cx="14" cy="100" r="3" fill={color} opacity="0.5" />
        </g>
      )}
      <g clipPath={`url(#${clip})`}>
        {image ? (
          <image href={image} x="12" y="12" width="176" height="176" preserveAspectRatio="xMidYMid slice" />
        ) : (
          <g className="av-body">
            <HairBack hair={look.hair} color={look.hairColor} />
            <path d="M34 200 Q40 150 100 148 Q160 150 166 200 Z" fill={color} opacity="0.85" />
            <rect x="88" y="128" width="24" height="26" rx="8" fill={look.skin} />
            <ellipse cx="100" cy="92" rx="38" ry="46" fill={look.skin} />
            <HairFront hair={look.hair} color={look.hairColor} />
            {id === 'nova' && <rect x="66" y="82" width="68" height="9" rx="4.5" fill={color} opacity="0.55" />}
            {id === 'atlas' && <path d="M66 112 Q100 140 134 112" fill="none" stroke={look.hairColor} strokeWidth="3" opacity="0.35" />}
            <g className="av-eyes">
              <ellipse className="av-eye" cx="84" cy="94" rx="5.5" ry="6.5" fill="#1a1a2e" />
              <ellipse className="av-eye" cx="116" cy="94" rx="5.5" ry="6.5" fill="#1a1a2e" />
            </g>
            <path d={`M74 ${80 - brow / 2} L94 ${80 + brow / 2}`} stroke={look.hairColor} strokeWidth="3" strokeLinecap="round" />
            <path d={`M126 ${80 - brow / 2} L106 ${80 + brow / 2}`} stroke={look.hairColor} strokeWidth="3" strokeLinecap="round" />
            <circle cx="72" cy="108" r="7" fill={color} opacity="0.18" />
            <circle cx="128" cy="108" r="7" fill={color} opacity="0.18" />
            {smile ? (
              <path d="M86 114 Q100 130 114 114" fill="none" stroke="#7a2e2e" strokeWidth="3.5" strokeLinecap="round" />
            ) : (
              <ellipse data-testid="avatar-mouth" cx="100" cy="118" rx={mood === 'speaking' ? 9 : 8} ry={open} fill="#7a2e2e" />
            )}
          </g>
        )}
      </g>
    </svg>
  );
}
