#include "Relaxation.h"
#include "Device.h"
#include "Data.h"

using namespace basic;
using namespace affection;
namespace ac
{
    RelaxationComputing::RelaxationComputing()
    {
        temp.index = 0;
        temp.wearFlagStore = nc::zeros<double>(1, 15);
        temp.relaxationTmp.weight = 0.5;
        nc::NdArray<double> features1 = {};
        nc::NdArray<double> features2 = {};
        nc::NdArray<double> features3 = {};
        temp.relaxationTmp.featuresStore.clear();
        temp.relaxationTmp.featuresStore.push_back(features1);
        temp.relaxationTmp.featuresStore.push_back(features2);
        temp.relaxationTmp.featuresStore.push_back(features3);
        temp.relaxationTmp.rulers = params::relaxationRuler;
    }

    RelaxationComputing::~RelaxationComputing()
    {
    }

    double RelaxationComputing::trigger(const basic::SessionCache &cache)
    {
        auto eeglPower = cache.eeglPower;
        auto eegrPower = cache.eegrPower;
        auto eegQuality = cache.eegQuality;
        auto eegPower = cache.eegPower;

        //算法执行
        // --佩戴标志记录
        if (eegQuality != dsp::EEGQuality::NONE)
            temp.wearFlagStore = nc::hstack({temp.wearFlagStore[nc::Slice(1,temp.wearFlagStore.size())], {1.}});
        else
            temp.wearFlagStore = nc::hstack({temp.wearFlagStore[nc::Slice(1,temp.wearFlagStore.size())], {0.}});

        //---佩戴判断
        if (nc::sum(temp.wearFlagStore[nc::Slice(temp.wearFlagStore.size()-15,temp.wearFlagStore.size())]).item() == 0)
        {
            temp.wearFlagStore = nc::zeros<double>(1, 15);
            temp.relaxationTmp.weight = 0.5;
            nc::NdArray<double> features1 = {};
            nc::NdArray<double> features2 = {};
            nc::NdArray<double> features3 = {};
            temp.relaxationTmp.featuresStore.clear();
            temp.relaxationTmp.featuresStore.push_back(features1);
            temp.relaxationTmp.featuresStore.push_back(features2);
            temp.relaxationTmp.featuresStore.push_back(features3);
            temp.relaxationTmp.relaxationStore = {};
            temp.relaxationTmp.rulers = params::relaxationRuler;
        }
        auto relaxation = handler::relaxationHandler(
            eegPower.alphaNorm(),
            eegPower.gammaNorm(),
            eeglPower.alphaNorm(),
            eegrPower.alphaNorm(),
            temp.relaxationTmp,
            10,
            100,
            20,
            0.4,
            50
        );
        temp.index += 1;

        temp.relaxationRec.push_back(relaxation);

        return nc::round(relaxation, 2);
    }

    RelaxationReportRes RelaxationComputing::report()
    {
        RelaxationReportRes res;
        res.relaxationAvg = 0;
        if (temp.relaxationRec.size() < 1)
            return res;

        auto relaxationNdArray = nc::NdArray<double>(temp.relaxationRec);
        res.relaxationRec = nc::round(relaxationNdArray, 2).toStlVector();
        auto relaxationMean = nc::mean(relaxationNdArray);
        res.relaxationAvg = double(nc::round(relaxationMean, 2).toStlVector().at(0));

        return res;
    }
}