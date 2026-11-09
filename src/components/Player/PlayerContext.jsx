import { createContext, useContext, useReducer, useRef } from 'react'

const PlayerContext = createContext(null)

export function playerReducer(state, action) {
  switch (action.type) {
    case 'add': {
      const items = [...state.items, action.track]
      return { items, index: action.play || state.index < 0 ? items.length - 1 : state.index,
        playing: action.play || state.playing }
    }
    case 'select': return { ...state, index: action.index, playing: true }
    case 'playing': return { ...state, playing: action.value }
    case 'next': return state.index < state.items.length - 1
      ? { ...state, index: state.index + 1, playing: true } : { ...state, playing: false }
    case 'previous': return state.index > 0 ? { ...state, index: state.index - 1, playing: true } : state
    default: return state
  }
}

export function PlayerProvider({ children }) {
  const [state, dispatch] = useReducer(playerReducer, { items: [], index: -1, playing: false })
  const sequence = useRef(0)
  const add = (track, play) => {
    if (!track.source) return
    dispatch({ type: 'add', track: { ...track, queueId: ++sequence.current }, play })
  }
  return <PlayerContext.Provider value={{ ...state, dispatch,
    play: track => add(track, true), enqueue: track => add(track, false),
    pause: () => dispatch({ type: 'playing', value: false }) }}>{children}</PlayerContext.Provider>
}

export function usePlayer() { return useContext(PlayerContext) }
