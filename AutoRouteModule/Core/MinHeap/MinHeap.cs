using System.Collections.Generic;
using AutoRouteModule.Utils;

namespace AutoRouteModule.Core
{
    public class MinHeap
    {
        private readonly List<int> _items = new List<int>();
        private readonly List<int> _priorities = new List<int>();

        public int Count => _items.Count;

        public void Clear()
        {
            _items.Clear();
            _priorities.Clear();
        }

        public void Push(int item, int priority)
        {
            _items.Add(item);
            _priorities.Add(priority);

            int index = _items.Count - 1;

            while (index > 0)
            {
                int parent = (index - 1) / 2;

                if (_priorities[parent] <= _priorities[index])
                    break;

                Swap(index, parent);
                index = parent;
            }
        }

        public int Pop()
        {
            int result = _items[0];

            int lastIndex = _items.Count - 1;

            _items[0] = _items[lastIndex];
            _priorities[0] = _priorities[lastIndex];

            _items.RemoveAt(lastIndex);
            _priorities.RemoveAt(lastIndex);

            if (_items.Count > 0)
                HeapifyDown(0);

            return result;
        }

        private void HeapifyDown(int index)
        {
            while (true)
            {
                int left = index * 2 + 1;
                int right = index * 2 + 2;
                int smallest = index;

                if (left < _items.Count && _priorities[left] < _priorities[smallest])
                    smallest = left;

                if (right < _items.Count && _priorities[right] < _priorities[smallest])
                    smallest = right;

                if (smallest == index)
                    break;

                Swap(index, smallest);
                index = smallest;
            }
        }

        private void Swap(int a, int b)
        {
            (_items[a], _items[b]) = (_items[b], _items[a]);
            (_priorities[a], _priorities[b]) = (_priorities[b], _priorities[a]);
        }
    }

    /// <summary>
    /// 위치 기반 MinHeap (A* 알고리즘 최적화용)
    /// UpdatePriority와 Contains를 지원하는 고성능 우선순위 큐
    /// </summary>
    public class MinHeap<TPosition>
    {
        private struct HeapNode
        {
            public float FCost;
            public TPosition Position;

            public HeapNode(float fCost, TPosition position)
            {
                FCost = fCost;
                Position = position;
            }
        }

        private List<HeapNode> _heap;
        private Dictionary<TPosition, int> _positionToIndex; // 빠른 Contains/UpdatePriority를 위한 인덱스 맵

        public int Count => _heap.Count;

        public MinHeap(int capacity = 1024)
        {
            _heap = new List<HeapNode>(capacity);
            _positionToIndex = new Dictionary<TPosition, int>(capacity);
        }

        public void Clear()
        {
            _heap.Clear();
            _positionToIndex.Clear();
        }

        public bool Contains(TPosition position)
        {
            return _positionToIndex.ContainsKey(position);
        }

        public void Add(float fCost, TPosition position)
        {
            var node = new HeapNode(fCost, position);
            _heap.Add(node);
            int index = _heap.Count - 1;
            _positionToIndex[position] = index;
            HeapifyUp(index);
        }

        public (float fCost, TPosition position) ExtractMin()
        {
            var min = _heap[0];
            _positionToIndex.Remove(min.Position);

            int lastIndex = _heap.Count - 1;
            if (lastIndex > 0)
            {
                _heap[0] = _heap[lastIndex];
                _positionToIndex[_heap[0].Position] = 0;
            }
            _heap.RemoveAt(lastIndex);

            if (_heap.Count > 0)
                HeapifyDown(0);

            return (min.FCost, min.Position);
        }

        public void UpdatePriority(TPosition position, float newFCost)
        {
            if (!_positionToIndex.TryGetValue(position, out int index))
                return;

            float oldFCost = _heap[index].FCost;
            var node = new HeapNode(newFCost, position);
            _heap[index] = node;

            if (newFCost < oldFCost)
                HeapifyUp(index);
            else if (newFCost > oldFCost)
                HeapifyDown(index);
        }

        private void HeapifyUp(int index)
        {
            while (index > 0)
            {
                int parentIndex = (index - 1) / 2;

                if (_heap[index].FCost >= _heap[parentIndex].FCost)
                    break;

                Swap(index, parentIndex);
                index = parentIndex;
            }
        }

        private void HeapifyDown(int index)
        {
            while (true)
            {
                int smallest = index;
                int leftChild = 2 * index + 1;
                int rightChild = 2 * index + 2;

                if (leftChild < _heap.Count && _heap[leftChild].FCost < _heap[smallest].FCost)
                    smallest = leftChild;

                if (rightChild < _heap.Count && _heap[rightChild].FCost < _heap[smallest].FCost)
                    smallest = rightChild;

                if (smallest == index)
                    break;

                Swap(index, smallest);
                index = smallest;
            }
        }

        private void Swap(int i, int j)
        {
            (_heap[i], _heap[j]) = (_heap[j], _heap[i]);

            _positionToIndex[_heap[i].Position] = i;
            _positionToIndex[_heap[j].Position] = j;
        }
    }
}