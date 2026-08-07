#include "VectorExtention.h"

namespace basic
{
    namespace tools
    {
        std::vector<uint8_t> cutArrs(std::vector<uint8_t> &arrs, int begin, int end)
        {
            std::vector<uint8_t> result;
            result.assign(arrs.begin() + begin, arrs.begin() + end);
            return result;
        }

        std::vector<double> cutArrs(std::vector<double> &arrs, int begin, int end)
        {
            std::vector<double> result;
            result.assign(arrs.begin() + begin, arrs.begin() + end);
            return result;
        }
    }
}