import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RowSelect } from './RowSelect'

afterEach(cleanup)

describe('RowSelect', () => {
  it('selects the active option with arrow keys and Enter', () => {
    const onChange = vi.fn()

    render(
      <RowSelect
        value="first"
        options={[
          { value: 'first', title: 'First' },
          { value: 'second', title: 'Second' },
        ]}
        onChange={onChange}
        ariaLabel="Mode"
        field="mode"
        title="First"
      />,
    )

    const trigger = screen.getByRole('button', { name: 'Mode' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    fireEvent.keyDown(trigger, { key: 'Enter' })

    expect(onChange).toHaveBeenCalledWith('second')
    expect(screen.queryByRole('listbox', { name: 'Mode' })).not.toBeInTheDocument()
  })
})
