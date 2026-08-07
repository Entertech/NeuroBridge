#include "Coherence.h"
#include "Data.h"
#include "MathTool.h"
#include <numeric>

using namespace basic;
using namespace affection;
namespace ac
{
    CoherenceComputing::CoherenceComputing()
    {
        temp.index = 0;
        temp.wearFlagStore = nc::zeros<double>(1, 15);
    }

    CoherenceComputing::~CoherenceComputing()
    {
    }

    double CoherenceComputing::trigger(const SessionCache &cache)
    {
        auto hrSyncCor = cache.hrSyncCor;
        auto hrQuality = cache.hrQuality;

        //算法执行
        //--佩戴标志记录
        if (hrQuality != dsp::HRQuality::INVALID)
            temp.wearFlagStore = nc::hstack({temp.wearFlagStore[nc::Slice(1, temp.wearFlagStore.size())], {1}});
        else
            temp.wearFlagStore = nc::hstack({temp.wearFlagStore[nc::Slice(1, temp.wearFlagStore.size())], {0}});

        //---佩戴判断
        if (nc::sum(temp.wearFlagStore[nc::Slice(temp.wearFlagStore.size() - 15, temp.wearFlagStore.size())]).item() == 0)
        {
            temp.wearFlagStore = nc::zeros<double>(1, 15);
            temp.coherenceTmp.coherenceStore = {};
            temp.coherenceTmp.hrvStore = {};
        }

        //--计算和谐度
        auto coherence = handler::coherenceHandler(
            hrSyncCor,
            temp.coherenceTmp,
            5

        );

        temp.index += 1;

        temp.coherenceRec.push_back(coherence);

        return nc::round(coherence, 2);
    }

    CoherenceReportRes CoherenceComputing::report()
    {
        CoherenceReportRes res;
        res.coherenceAvg = 0;
        res.coherenceDuration = 0;
        if (temp.coherenceRec.size() < 30)
        {
            return res;
        }

        auto coherenceNdArray = nc::NdArray<double>(temp.coherenceRec);
        res.coherenceRec = nc::round(coherenceNdArray, 2).toStlVector();
        auto coherenceMean = nc::mean(coherenceNdArray);
        res.coherenceAvg = double(nc::round(coherenceMean, 2).toStlVector().at(0));

        // Set the upload cycle
        int uploadCycle = 1;
        // Calculate the factors for the minimum calculations
        double factorForHalfSmoothLen = 8 / (0.6 * uploadCycle);
        double factorForShiftLeft = 20 / (0.6 * uploadCycle);
        double factorForShiftRight = 4 / (0.6 * uploadCycle);
        // Calculate the third of the coherence record length
        int thirdOfCoherenceRecLength = static_cast<int>(res.coherenceRec.size()) / 3;
        // Calculate the minimum for halfSmoothLen, shiftLeft and shiftRight
        int halfSmoothLen = std::min(static_cast<int>(std::ceil(factorForHalfSmoothLen)), thirdOfCoherenceRecLength);
        int shiftLeft = std::min(static_cast<int>(std::ceil(factorForShiftLeft)), thirdOfCoherenceRecLength);
        int shiftRight = std::min(static_cast<int>(std::ceil(factorForShiftRight)), thirdOfCoherenceRecLength);
        // ------和谐度平滑
        auto coherenceSmooth = mathtool::smoothCurveCal(res.coherenceRec, halfSmoothLen);
        for (int i = 0; i < res.coherenceRec.size(); i++) {
            if (res.coherenceRec[i] == 0) {
                coherenceSmooth[i] = 0;
            }
        }
        // ------和谐区间计算
        vectori coherenceFlagOri;
        for (auto &coherence : coherenceSmooth)
        {
            if (coherence > 50)
                coherenceFlagOri.push_back(1);
            else
                coherenceFlagOri.push_back(0);
        }
        // ------区间扩展
        std::vector<int> coherenceFlagUnion(coherenceSmooth.size(), 0);
        // Loop over the range of shiftLeft
        for (int shift = 0; shift < shiftLeft; ++shift) {

            // Create a copy of coherenceFlagOri
            std::vector<int> coherenceFlagLeft = coherenceFlagOri;

            // Append 'shift' copies of the last element of coherenceFlagOri to the end of coherenceFlagLeft
            coherenceFlagLeft.insert(coherenceFlagLeft.end(), shift, coherenceFlagOri.back());

            // Create a new vector with the elements of coherenceFlagLeft starting from 'shift' position
            std::vector<int> shiftedCoherenceFlagLeft;
            shiftedCoherenceFlagLeft.assign(coherenceFlagLeft.begin() + shift, coherenceFlagLeft.end());
            // Add the elements of shiftedCoherenceFlagLeft to the corresponding elements of coherenceFlagUnion
            std::transform(coherenceFlagUnion.begin(), coherenceFlagUnion.end(), shiftedCoherenceFlagLeft.begin(), coherenceFlagUnion.begin(), std::plus<int>());
        }

        for (int shift = 0; shift < shiftRight; ++shift) {
            // Create a vector with 'shift' copies of the first element of coherenceFlagOri
            std::vector<int> temp(shift, coherenceFlagOri.front());
            // Concatenate coherenceFlagOri to the end of the temporary vector
            std::vector<int> coherenceFlagRight;
            coherenceFlagRight.reserve(temp.size() + coherenceFlagOri.size());
            coherenceFlagRight.insert(coherenceFlagRight.end(), temp.begin(), temp.end());
            coherenceFlagRight.insert(coherenceFlagRight.end(), coherenceFlagOri.begin(), coherenceFlagOri.end());
            // Slice the vector from 'shift' position to the end
            std::vector<int> sliced(coherenceFlagRight.begin() + shift, coherenceFlagRight.end());
            // Add the elements of sliced to the corresponding elements of coherenceFlagUnion
            std::transform(coherenceFlagUnion.begin(), coherenceFlagUnion.end(), sliced.begin(), coherenceFlagUnion.begin(), std::plus<int>());
        }
        vectori coherenceFlag;
        int coherenceSum = 0;
        for (int i = 0; i < coherenceFlagUnion.size(); i++) {
            if (coherenceFlagUnion[i] > 0) {
                coherenceFlag.push_back(1);
                coherenceSum++;
            } else
            {
                coherenceFlag.push_back(0);
            }
        }
        res.flagRec = coherenceFlag;
        res.coherenceDuration = static_cast<int>(ceil(0.6*coherenceSum));

        return res;
    }
}