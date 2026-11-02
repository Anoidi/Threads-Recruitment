import re
import sys

def add_nullable_to_mapped_column(line):
    # Skip if it's a relationship line
    if 'relationship(' in line:
        return line
    # Check if the line contains the pattern we want to change
    if 'Mapped[' in line and ' | None]' in line and 'mapped_column' in line:
        # Split the line at the first '=' to get left and right parts
        if '=' not in line:
            return line
        left, right = line.split('=', 1)
        # Fix the type annotation in the left part: remove the ' | None'
        left = re.sub(r'(Mapped\[)([^|]+) \| None\]', r'\1\2]', left)
        # Now process the right part to add nullable=True to mapped_column
        # We'll find the mapped_column call and insert nullable=True before the closing parenthesis
        # We'll use a regex to capture the content inside mapped_column(...) 
        # but we must be cautious of nested parentheses.
        # Instead, we can do: replace the first occurrence of 'mapped_column(' with 'mapped_column(nullable=True, '
        # but that would break if there are already arguments.
        # Better: find the mapped_column(...) and then add ', nullable=True' before the closing parenthesis.
        # We'll assume that the mapped_column call is the first one in the right part and that there are no nested mapped_column calls.
        # We'll look for the pattern: mapped_column( ... ) and we'll try to add the argument.
        # We'll use a simple approach: if there's no 'nullable=' already in the mapped_column call, we add it.
        # We'll find the index of 'mapped_column(' and then find the matching closing parenthesis.
        # This is more complex. Given the time, we'll use a simpler regex that works for most cases.
        # We'll search for 'mapped_column(' and then until the next ')' that is not inside a nested pair.
        # We'll do a heuristic: we'll look for the first 'mapped_column(' and then find the next ')' that is at the same nesting level.
        # We'll implement a simple stack for parentheses.
        # However, note that the line might have other function calls. We'll assume that the mapped_column call is the outermost.
        # We'll find the index of 'mapped_column(' and then traverse forward to find the matching closing parenthesis.
        start = right.find('mapped_column(')
        if start == -1:
            # If we don't find it, return the line as is (after fixing the annotation)
            return left + '=' + right
        # We'll now find the matching closing parenthesis for the one at start.
        # We know the opening parenthesis is at start + len('mapped_column') = start + 12
        pos = start + 12  # points to the opening parenthesis
        stack = 1
        i = pos + 1
        while i < len(right) and stack > 0:
            if right[i] == '(':
                stack += 1
            elif right[i] == ')':
                stack -= 1
            i += 1
        if stack != 0:
            # Unbalanced parentheses, return as is
            return left + '=' + right
        # Now, the closing parenthesis is at i-1.
        # We want to insert ', nullable=True' just before this closing parenthesis.
        # But we must check if there is already a nullable argument.
        # We'll look at the substring inside the parentheses: from pos+1 to i-1 (exclusive of the parentheses)
        inner = right[pos+1:i-1]
        if 'nullable=' not in inner:
            # We'll insert ', nullable=True' before the closing parenthesis.
            # We'll build the new right part: 
            #   right[0:i-1] + ', nullable=True' + right[i-1:]
            new_right = right[:i-1] + ', nullable=True' + right[i-1:]
            return left + '=' + new_right
        else:
            # Already has nullable, so we just return the line after fixing the annotation.
            return left + '=' + right
    else:
        # If the line doesn't match our pattern, return it unchanged.
        return line

def main():
    filename = 'backend/app/models.py'
    with open(filename, 'r') as f:
        lines = f.readlines()
    
    new_lines = []
    for line in lines:
        new_lines.append(process_line(line))
    
    with open(filename, 'w') as f:
        f.writelines(new_lines)

def process_line(line):
    # Skip relationship lines
    if 'relationship(' in line:
        return line
    # Check if the line contains the pattern we want to change
    if 'Mapped[' in line and ' | None]' in line and 'mapped_column' in line:
        return add_nullable_to_mapped_column(line)
    return line

if __name__ == '__main__':
    main()