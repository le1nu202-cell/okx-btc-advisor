import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => cleanup())

class TestResizeObserver implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

class SilentWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3

  readonly CONNECTING = 0
  readonly OPEN = 1
  readonly CLOSING = 2
  readonly CLOSED = 3
  readonly url: string
  readonly protocol = ''
  readonly extensions = ''
  binaryType: BinaryType = 'blob'
  bufferedAmount = 0
  readyState = SilentWebSocket.CONNECTING
  onopen: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null

  constructor(url: string | URL) {
    this.url = String(url)
  }

  close(): void {
    this.readyState = SilentWebSocket.CLOSED
  }

  send(): void {}
  addEventListener(): void {}
  removeEventListener(): void {}
  dispatchEvent(): boolean { return true }
}

Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, writable: true, value: TestResizeObserver })
Object.defineProperty(globalThis, 'WebSocket', { configurable: true, writable: true, value: SilentWebSocket })
Object.defineProperty(window, 'confirm', { configurable: true, writable: true, value: () => true })
