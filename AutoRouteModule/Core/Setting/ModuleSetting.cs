
namespace AutoRouteModule.Core
{
    public static class AutoRouteModuleDefine
    {
        public const int TURN_PENALTY_DEFAULT = 0;
        public const int VERTICAL_PENALTY_DEFAULT = 0;
        public const int HORIZONTAL_PENALTY_DEFAULT = 0;
        public const int MAX_SEARCH_NODES_DEFAULT = 0;

        public const int TURN_PENALTY_MAX = 100;
        public const int VERTICAL_PENALTY_MAX = 20;
        public const int HORIZONTAL_PENALTY_MAX = 20;

    }

    public static class AutoRouteModuleSetting
    {

        // Tree
        public static float octreeMinLeafSize = 6.0f;
        public static int octreeMaxDepth = 8;
        public static int bvhThreshold = 32;

    }
}
