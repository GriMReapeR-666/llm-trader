"""Genetic Operators for Alpha Expression Trees.

Implements crossover and mutation operators for evolving alpha expressions
using hierarchical genetic algorithms.

References:
    Jung, J. (2026). "Alpha Factor Mining Using Hierarchical Genetic Algorithms
    with Quality-Diversity Selection." SSRN.
"""
from __future__ import annotations

import random
from typing import List, Optional, Tuple

import numpy as np

from .expression_tree import (
    AlphaExpression,
    FIELDS,
    CONSTANTS,
    WINDOWS,
    UNARY_ARITHMETIC,
    BINARY_ARITHMETIC,
    TS_UNARY,
    CS_UNARY,
    create_leaf,
    create_unary,
    create_binary,
)
from .expression_generator import ExpressionGenerator


class GeneticOperators:
    """Crossover and mutation for alpha expression trees."""
    
    def __init__(self, 
                 mutation_rate: float = 0.1,
                 crossover_rate: float = 0.7,
                 subtree_mutation_rate: float = 0.05,
                 max_depth: int = 5,
                 seed: Optional[int] = None):
        """Initialize genetic operators.
        
        Args:
            mutation_rate: Probability of point mutation
            crossover_rate: Probability of crossover
            subtree_mutation_rate: Probability of subtree replacement
            max_depth: Maximum allowed tree depth
            seed: Random seed for reproducibility
        """
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.subtree_mutation_rate = subtree_mutation_rate
        self.max_depth = max_depth
        self.generator = ExpressionGenerator(seed=seed)
    
    def crossover(self, parent1: AlphaExpression, 
                  parent2: AlphaExpression) -> Tuple[AlphaExpression, AlphaExpression]:
        """Subtree crossover: swap random subtrees between parents.
        
        Args:
            parent1: First parent expression
            parent2: Second parent expression
            
        Returns:
            Tuple of two offspring expressions
        """
        # Copy parents
        child1 = parent1.copy()
        child2 = parent2.copy()
        
        # Check depth constraint
        if child1.max_depth() + child2.max_depth() > self.max_depth * 2:
            return child1, child2
        
        # Select random nodes from each parent
        node1, parent1_ref, idx1 = child1.get_random_node()
        node2, parent2_ref, idx2 = child2.get_random_node()
        
        # Swap subtrees
        if parent1_ref is not None and parent2_ref is not None:
            # Get copies of subtrees
            subtree1 = node1.copy()
            subtree2 = node2.copy()
            
            # Replace in children arrays
            parent1_ref.children[idx1] = subtree2
            parent2_ref.children[idx2] = subtree1
        
        return child1, child2
    
    def mutate(self, expression: AlphaExpression,
               mutation_rate: Optional[float] = None) -> AlphaExpression:
        """Point mutation: randomly change operator, field, or window.
        
        Args:
            expression: Expression to mutate
            mutation_rate: Override mutation rate
            
        Returns:
            Mutated expression
        """
        if mutation_rate is None:
            mutation_rate = self.mutation_rate
        
        # Copy expression
        mutated = expression.copy()
        
        # Recursively mutate nodes
        self._mutate_node(mutated, mutation_rate)
        
        return mutated
    
    def _mutate_node(self, node: AlphaExpression, mutation_rate: float):
        """Recursively apply point mutations to nodes."""
        # Apply mutation to current node
        if random.random() < mutation_rate:
            self._mutate_single_node(node)
        
        # Recurse to children
        for child in node.children:
            self._mutate_node(child, mutation_rate)
    
    def _mutate_single_node(self, node: AlphaExpression):
        """Apply mutation to a single node."""
        if node.field is not None:
            # Mutate field
            if random.random() < 0.3:
                node.field = random.choice(FIELDS)
        
        elif node.constant is not None:
            # Mutate constant
            if random.random() < 0.3:
                node.constant = random.choice(CONSTANTS)
        
        elif node.window is not None:
            # Mutate window
            if random.random() < 0.3:
                node.window = random.choice(WINDOWS)
        
        # Mutate operator
        if random.random() < 0.2:
            self._mutate_operator(node)
    
    def _mutate_operator(self, node: AlphaExpression):
        """Mutate the operator of a node."""
        current_op = node.operator
        
        # Find compatible operator
        if current_op in UNARY_ARITHMETIC:
            new_op = random.choice(UNARY_ARITHMETIC)
            node.operator = new_op
        
        elif current_op in BINARY_ARITHMETIC:
            new_op = random.choice(BINARY_ARITHMETIC)
            node.operator = new_op
        
        elif current_op in TS_UNARY:
            new_op = random.choice(TS_UNARY)
            node.operator = new_op
        
        elif current_op in CS_UNARY:
            new_op = random.choice(CS_UNARY)
            node.operator = new_op
    
    def subtree_mutation(self, expression: AlphaExpression,
                         max_depth: Optional[int] = None) -> AlphaExpression:
        """Replace random subtree with new random expression.
        
        Args:
            expression: Expression to mutate
            max_depth: Override max depth
            
        Returns:
            Mutated expression
        """
        if max_depth is None:
            max_depth = self.max_depth
        
        # Copy expression
        mutated = expression.copy()
        
        # Select random node
        node, parent, idx = mutated.get_random_node()
        
        # Generate new subtree
        new_depth = max(1, max_depth - node.depth)
        new_subtree = self.generator.generate_expression(max_depth=new_depth)
        
        # Replace subtree
        if parent is not None:
            parent.children[idx] = new_subtree
        else:
            # Replace root
            mutated = new_subtree
        
        return mutated
    
    def select_parents(self, population: List[AlphaExpression],
                       fitness_scores: List[float],
                       tournament_size: int = 3) -> AlphaExpression:
        """Tournament selection: select best from random subset.
        
        Args:
            population: List of expressions
            fitness_scores: Corresponding fitness (IC) scores
            tournament_size: Number of candidates in tournament
            
        Returns:
            Selected parent expression
        """
        # Select random candidates
        indices = random.sample(range(len(population)), 
                               min(tournament_size, len(population)))
        
        # Find best candidate by |IC|: sign is free (invert the position),
        # so tournament pressure must reward magnitude, not direction.
        best_idx = max(indices, key=lambda i: abs(fitness_scores[i]))
        
        return population[best_idx]
    
    def evolve_generation(self, population: List[AlphaExpression],
                         fitness_scores: List[float],
                         elite_size: int = 10) -> List[AlphaExpression]:
        """Evolve one generation of the population.
        
        Args:
            population: Current population
            fitness_scores: Fitness scores for each expression
            elite_size: Number of elite individuals to preserve
            
        Returns:
            New population
        """
        n = len(population)
        
        # Sort by |fitness| (|IC|): sign is tradeable either way.
        sorted_indices = sorted(range(n), key=lambda i: abs(fitness_scores[i]), reverse=True)
        
        # Preserve elites
        new_population = [population[i].copy() for i in sorted_indices[:elite_size]]
        
        # Generate rest of population
        while len(new_population) < n:
            # Select parents
            parent1 = self.select_parents(population, fitness_scores)
            parent2 = self.select_parents(population, fitness_scores)
            
            # Apply crossover
            if random.random() < self.crossover_rate:
                child1, child2 = self.crossover(parent1, parent2)
            else:
                child1, child2 = parent1.copy(), parent2.copy()
            
            # Apply mutation
            if random.random() < self.mutation_rate:
                child1 = self.mutate(child1)
            if random.random() < self.mutation_rate:
                child2 = self.mutate(child2)
            
            # Apply subtree mutation
            if random.random() < self.subtree_mutation_rate:
                child1 = self.subtree_mutation(child1)
            if random.random() < self.subtree_mutation_rate:
                child2 = self.subtree_mutation(child2)
            
            # Add to new population
            if len(new_population) < n:
                new_population.append(child1)
            if len(new_population) < n:
                new_population.append(child2)
        
        return new_population[:n]
    
    def repair_depth(self, expression: AlphaExpression, 
                     max_depth: int) -> AlphaExpression:
        """Repair expression to satisfy depth constraint.
        
        Args:
            expression: Expression to repair
            max_depth: Maximum allowed depth
            
        Returns:
            Repaired expression
        """
        if expression.max_depth() <= max_depth:
            return expression
        
        # Replace deep subtrees with leaves
        repaired = expression.copy()
        self._repair_depth_recursive(repaired, 0, max_depth)
        
        return repaired
    
    def _repair_depth_recursive(self, node: AlphaExpression, 
                               current_depth: int, max_depth: int):
        """Recursively repair depth violations."""
        if current_depth >= max_depth - 1:
            # Replace children with leaves
            node.children = [create_leaf(field=random.choice(FIELDS)) 
                           for _ in node.children]
            node.window = None
            return
        
        for child in node.children:
            self._repair_depth_recursive(child, current_depth + 1, max_depth)
