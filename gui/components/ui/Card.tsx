import React from 'react';

interface CardProps {
  children: React.ReactNode;
  title?: string;
  className?: string;
  headerAction?: React.ReactNode;
}

export const Card: React.FC<CardProps> = ({ children, title, className = '', headerAction }) => {
  return (
    <div className={`bg-terminal-panel/90 backdrop-blur-md border border-terminal-border rounded-lg overflow-hidden flex flex-col ${className}`}>
      {title && (
        <div className="px-4 py-3 border-b border-terminal-border bg-terminal-panel/95 flex justify-between items-center">
          <h3 className="text-terminal-text font-semibold text-sm tracking-wide uppercase">{title}</h3>
          {headerAction}
        </div>
      )}
      <div className="p-4 flex-1 overflow-auto custom-scrollbar">
        {children}
      </div>
    </div>
  );
};