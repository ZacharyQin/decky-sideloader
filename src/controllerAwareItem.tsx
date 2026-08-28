import { Focusable } from "@decky/ui";
import { ReactNode, useState } from "react";

type ControllerAwareItemProps = {
  label: string;
  description?: ReactNode;
  onPress: () => void;
  onSecondary?: () => void;
  onCancel?: () => void;
};

export function ControllerAwareItem({
  label,
  description,
  onPress,
  onSecondary,
  onCancel,
}: ControllerAwareItemProps) {
  const [focused, setFocused] = useState(false);

  return (
    <Focusable
      role="button"
      aria-label={label}
      onActivate={onPress}
      onOKButton={onPress}
      onSecondaryButton={onSecondary}
      onCancelButton={onCancel}
      onOKActionDescription="Open"
      onSecondaryActionDescription={onSecondary ? "More" : undefined}
      onCancelActionDescription={onCancel ? "Back" : undefined}
      onGamepadFocus={() => setFocused(true)}
      onGamepadBlur={() => setFocused(false)}
    >
      <div data-focused={focused}>
        <div>{label}</div>
        {description ? <div>{description}</div> : null}
      </div>
    </Focusable>
  );
}
