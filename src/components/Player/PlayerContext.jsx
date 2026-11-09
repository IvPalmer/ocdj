import { createContext, useContext, useReducer, useRef } from 'react'

const PlayerContext = createContext(null)

export function playerReducer(state, action) {
  switch (action.type) {
    case 'add': {
      const items = [...state.items, action.track]
      return { ...state, items, index: action.play || state.index < 0 ? items.length - 1 : state.index,
        audible: action.play ? false : state.audible,
        playing: action.play || state.playing }
    }
    case 'select': return action.index === state.index && state.playing ? state
      : { ...state, index: action.index, playing: true, audible: false }
    case 'playing': return { ...state, playing: action.value, audible: false }
    case 'source-playing':
      return state.items[state.index]?.queueId === action.queueId &&
        (state.playing !== action.value || state.audible !== action.value)
        ? { ...state, playing: action.value, audible: action.value } : state
    case 'next': return state.index < state.items.length - 1
      ? { ...state, index: state.index + 1, playing: true, audible: false }
      : { ...state, playing: false, audible: false }
    case 'previous': return state.index > 0 ? { ...state, index: state.index - 1, playing: true, audible: false } : state
    default: return state
  }
}

export function PlayerProvider({ children }) {
  const [state, dispatch] = useReducer(playerReducer, { items: [], index: -1, playing: false, audible: false })
  const sequence = useRef(0)
  const add = (track, play) => {
    if (!track.source) return
    if (track.source.kind === 'mac') track = { ...track,
      description: 'Indexed Mac library · temporary MP3 playback copy · Mac connector required' }
    dispatch({ type: 'add', track: { ...track, queueId: ++sequence.current }, play })
  }
  return <PlayerContext.Provider value={{ ...state, dispatch,
    play: track => add(track, true), enqueue: track => add(track, false),
    pause: () => dispatch({ type: 'playing', value: false }) }}>{children}</PlayerContext.Provider>
}

export function usePlayer() { return useContext(PlayerContext) }
